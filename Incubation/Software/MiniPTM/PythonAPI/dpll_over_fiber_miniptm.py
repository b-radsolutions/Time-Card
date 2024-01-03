import time
from collections import deque
from board_miniptm import *
from renesas_cm_registers import *
import random



"""
For DPLL over fiber, it relies on three mechanisms:
    1. TOD transmission by the encoders
        a. Each encoder has it's own TOD
        b. effectively 4 TX buffers
    2. TOD reception by the decoders
        a. Only one RX buffer, PWM_TOD 0xce80
        b. only seconds portion is used, but 6 bytes of second data
    3. PWM User Data transfer
        a. Only one buffer for TX and RX
        b. 128 byte 


For anything more than a few bytes, PWM User Data is the only reasonable mechanism

Receive path is most constrained
    1. Only one decoder should be enabled at a time
    2. It may take time for a new frame to come in, even if other side wants to transmit

Design mainly around optimizing the receive Path

Goal of communication with TOD transmission / reception is to negotiate User data transfer

Define protocol using upper two bytes of TOD, TOD_SEC[5] and TOD_SEC[4]

Assume these second fields are dedicated, only using 4 bytes of seconds, 132 years good enough

Protocol is a handshake process
    1. One or both sides sends an initial (0x0) state data TOD with desired action and a random byte

    2. If both random numbers are the same, then both sides must change their number and wait for other side to change too

    3. Whichever side has the lowest random byte wins priority for it's action to be completed

    4. If the winning side wants to query something, then it readies it's PWM user data for reception, otherwise it readies it for transmission

    5. LOSING SIDE
        a. If the request is a query, the losing side readies its PWM User data for transmission, but doesnt sent it yet, it sends accept (0x1) state data TOD
        b. If the request is a write, the losing side readies its PWM User data for reception (clearing first byte of buffer), and sends accept (0x1) state data TOD

    6. The winning side, upon reception of accept, also sends accept (0x1) state data 
        a. If the request is a query, it waits for PWM User data reception completion 
        b. If the request is a write, it goes through process of sending user data

    7. If the request is a query or a write, the losing side sends end state (0x2) TOD first, and uses random byte field to send its PWM User status
        a. If the request is a write, losing side RX side stores this full buffer and passes to higher layer, 128 byte buffer format defined elsewhere
        b. If the request is a query, winner side RX side stores this full buffer and passes to higher layer, 128 byte buffer format defined elsewhere

    8. Two options for winner
        a. If done with transmit , go back to normal TOD transmission without data flag set
        b. If more data to transmit, send initial (0x0) state data TOD with desired action. Repeated start kind of behavior, go back to 4

    9. For receiver
        a. If see data flag go away, then transaction is completed, handle data buffer however that data buffer is defined
        b. If see data flag stay and state go back to 0x0, then go back to step 4 here

Defined in the layout in registers map but description is here PWM_RX_INFO_LAYOUT

TOD_SEC[5][7] = Data Flag, set when the TOD field is being aliased for handshaking, 0 when normal TOD
TOD_SEC[5][6:5] = Handshake flag
TOD_SEC[5][0:4] = Transaction ID
    0x0 = Read chip info
            a. Status of all inputs, STATUS.INX_MON_STATUS bytes for 0-15 (16 bytes)
            b. DPLL Status of DPLLs , STATUS.DPLLX_STATUS bytes for 0-3 (4 bytes)
            c. Input frequency monitor info, STATUS.INX_MON_FREQ_STATUS for 0-15, (32 bytes)
            d. A name string, 16 bytes including null
            e. TOD delta seen between received TOD frame and local TOD counter, used for round trip calculations (11 bytes)
            f. 

    0x1 = Write to board
            a. LED values bit-wise, 1 byte
            b. Force follow this requester, 1 byte, must be 0xa5 for this function, otherwise doesn't use
                Follow frequency and TOD and PPS

TOD_SEC[4][7:0] = Random value for winner / loser determination, PWM User data state from loser upon end state

"""




"""
RX is the most constrained , so it needs the most logic

1. Need to round robin through decoders often
2. Want to detect
    a. is there a PWM encoder on the other side
    b. is the PWM encoder on the other side trying to initiate a request
"""


# basically control TOD , a single PWM Encoder, and a single decoder
class dpof_single_channel():
    IDLE = 0
    RX_SLAVE = 1
    TRANSMIT_START = 2
    TRANSMIT_WON = 3
    TRANSMIT_WRITE = 4
    TRANSMIT_QUERY = 5
    TRANSMIT_DONE_WAIT = 6
    RX_SLAVE_RESPOND_QUERY = 7
    RX_SLAVE_RESPOND_QUERY_WAIT_FIFO_TX = 8
    RX_SLAVE_RESPOND_QUERY_WAIT_FIFO_TX_DONE = 9
    RX_SLAVE_WAIT_WRITE = 10
    RX_SLAVE_DONE_WAIT = 11

    def __init__(self, board, tod_num = 0, encoder_num=0, decoder_num=0, decoder_time_slice_sec=5):
        self.board = board

        # tx variables
        self.tod_num = tod_num
        self.encoder = encoder_num
        self.tx_rand_num = 0
        self.tx_enabled = False
        self.time_tx_enabled = False
        self.current_transaction_id_tx = 0
        self.fifo_to_send = []


        # rx variables
        self.decoder = decoder_num
        # by default disable decoder
        self.board.dpll.modules["PWMDecoder"].write_field(self.decoder,
              "PWM_DECODER_CMD", "ENABLE", 0)
        self.rx_enabled = False
        self.decoder_time_slice_sec = decoder_time_slice_sec
        self.last_data_this_decoder = []

        self.fifo_grant = False
        self.state = dpof_single_channel.IDLE 


    def grant_fifo_control(self):
        self.fifo_grant = True

    def release_fifo_control(self):
        self.fifo_grant = False

    def get_fifo_grant_status(self):
        return self.fifo_grant

    def run_idle_state(self):
        # only transition out of idle is if RX data detected
        # TX transaction is "async"
        data = self.check_decoder_new_data() 
        if ( len(data) == 0 ):
            # Case 1: Decoder inactive
            return self.disable_decoder_if_time_over(), 0
        else:
            # Case 2: Decoder active
            # check decoder value
            top_byte = data[-1]
            rand_id_rx = data[-2]
            data_flag = (top_byte >> 7) & 0x1
            handshake_state_rx = (top_byte >> 5) & 0x3
            transaction_id_rx = top_byte & 0x1f
            if( not data_flag ):
                return self.disable_decoder_if_time_over(), False
            else:
                self.state = dpof_single_channel.RX_SLAVE
                self.master_request = transaction_id_rx
                print(f"Going to rx slave state from idle, master request = 0x{self.master_request:02x}")
                return self.disable_decoder_if_time_over(), True

       

    def run_rx_slave_state(self):
        #only transition out of this state is if fifo grant is granted by higher level
        # or TX API called
        if ( self.fifo_grant ):
            print(f"RX slave state got fifo grant")
            # got the grant, now I can use PWM FIFO for dpll over fiber as slave
            if ( self.is_transaction_id_query(self.master_request) ):
                # write 0x1 to master
                self.start_tx(0x1, self.master_request, [])

                # master request is a query, don't need fifo or decoder locked yet
                self.state = dpof_single_channel.RX_SLAVE_RESPOND_QUERY

                print(f"RX slave state go to respond query")
                return self.disable_decoder_if_time_over(), True
            else:
                # master request is a write
                # NEED TO HOLD FIFO AND DECODER 
                self.state = dpof_single_channel.RX_SLAVE_WAIT_WRITE

                # set fifo to receive, 0x0 for idle
                self.board.dpll.modules["PWM_USER_DATA"].write_reg(0,
                        "PWM_USER_DATA_PWM_USER_DATA_CMD_STS", 0x0)
                print(f"RX slave state go to wait write")
                return True, True
        return self.disable_decoder_if_time_over(), False


    def run_rx_slave_respond_query(self):
        # only a wait state, wait for 0x1 from master
        data = self.check_decoder_new_data() 
        if ( len(data) == 0 ):
            # Case 1: Decoder inactive
            return self.disable_decoder_if_time_over(), False
        else:
            # Case 2: Decoder active
            # check decoder value
            top_byte = data[-1]
            rand_id_rx = data[-2]
            data_flag = (top_byte >> 7) & 0x1
            handshake_state_rx = (top_byte >> 5) & 0x3
            transaction_id_rx = top_byte & 0x1f
            print(f"Run rx slave respond query handshake_state_rx id={handshake_state_rx}")

            if ( handshake_state_rx == 0x1 ):
                # Got 0x1, write to PWM FIFO and send it, wait for TX Completion on FIFO
                self.fifo_to_send = self.get_fifo_respond_to_query(transaction_id_rx)
                print(f"Will send {len(self.fifo_to_send)} bytes of PWM")
                self.board.dpll.modules["PWM_USER_DATA"].write_reg(0,
                        "PWM_USER_DATA_PWM_USER_DATA_SIZE", len(self.fifo_to_send))

                self.board.dpll.modules["PWM_USER_DATA"].write_reg(0,
                        "PWM_USER_DATA_PWM_USER_DATA_CMD_STS", 0x1) #send transmission request

                # wait for TX completion on FIFO
                self.state = dpof_single_channel.RX_SLAVE_RESPOND_QUERY_WAIT_FIFO_TX 

                print(f"Go to rx slave respond query wait fifo tx")

                return self.disable_decoder_if_time_over(), True
        return self.disable_decoder_if_time_over(), True
         
    def run_rx_slave_respond_query_wait_fifo_tx(self):
        # only a wait state, wait for PWM FIFO to say TX completed or errored or something

        fifo_status = self.board.dpll.modules["PWM_USER_DATA"].read_reg(0,
                "PWM_USER_DATA_PWM_USER_DATA_CMD_STS")
        if (fifo_status == 0x3): # got tx ack , can send data now
            print(f"RX slave respond query wait fifo tx, got tx ack")
            print(f" Sending fifo {self.fifo_to_send}")
            for i in range(len(self.fifo_to_send)):
                reg_name = f"BYTE_OTP_EEPROM_PWM_BUFF_{i}"
                self.board.dpll.modules["EEPROM_DATA"].write_reg(0,
                        reg_name, self.fifo_to_send[i] )

            # start transmission
            self.board.dpll.modules["PWM_USER_DATA"].write_reg(0,
                    "PWM_USER_DATA_PWM_USER_DATA_CMD_STS", 0x2) #sent transmission request

            self.state = dpof_single_channel.RX_SLAVE_RESPOND_QUERY_WAIT_FIFO_TX_DONE
        elif ( fifo_status > 0x3 ):
            print(f"GOT FIFO STATUS BAD, {fifo_status}")

        # don't need decoder, but need PWM fifo
        return self.disable_decoder_if_time_over(), True



    def run_rx_slave_respond_query_wait_fifo_tx_done(self):
        # simple wait state, wait for PWM FIFO to send it sent out the data
        fifo_status = self.board.dpll.modules["PWM_USER_DATA"].read_reg(0,
                "PWM_USER_DATA_PWM_USER_DATA_CMD_STS")

        if ( fifo_status == 0x5 ):
            # once complete, send 0x2 and go to done wait
            print(f"Fifo status good, PWM transmission successful!")
            self.start_tx(0x2, self.master_request, [])
            self.state = dpof_single_channel.RX_SLAVE_DONE_WAIT

            # done with FIFO
            return self.disable_decoder_if_time_over(), False
        else:
            print(f"Fifo status not done yet, respond query wait fifo tx done")
        return self.disable_decoder_if_time_over(), True


    
    
    def run_rx_slave_done_wait(self):
        # sent 0x2, wait for data flag to go away or state to change to 0x0
        data = self.check_decoder_new_data() 
        if ( len(data) == 0 ):
            # Case 1: Decoder inactive
            return self.disable_decoder_if_time_over(), 0
        else:
            # Case 2: Decoder active
            # check decoder value
            top_byte = data[-1]
            rand_id_rx = data[-2]
            data_flag = (top_byte >> 7) & 0x1
            handshake_state_rx = (top_byte >> 5) & 0x3
            transaction_id_rx = top_byte & 0x1f
            if( not data_flag ):
                self.state = dpof_single_channel.IDLE
            else:
                if ( handshake_state_rx == 0x0 ):
                    self.state = dpof_single_channel.RX_SLAVE
        return self.disable_decoder_if_time_over(), False

    def run_rx_slave_wait_write(self):
        # wait for PWM FIFO to fill up 
        fifo_status = self.board.dpll.modules["PWM_USER_DATA"].read_reg(0,
                "PWM_USER_DATA_PWM_USER_DATA_CMD_STS")
        if ( fifo_status == 0xb ):
            print(f"PWM User data reception successful!")
            # send 0x2 and wait
            self.start_tx(0x2, self.master_request, [])
            self.state = dpof_single_channel.RX_SLAVE_DONE_WAIT
        else:
            print(f"PWM User data reception not done yet! {fifo_status:02x}")

        # need to hold decoder and fifo
        return True, True

    def run_transmit_start_state(self):
        # come to this state async when transmit is started from idle state
        # check the RX data if data flag detected
        data = self.check_decoder_new_data() 
        if ( len(data) == 0 ):
            # Case 1: Decoder inactive
            return self.disable_decoder_if_time_over(), False
        else:
            # Case 2: Decoder active
            # check decoder value
            top_byte = data[-1]
            rand_id_rx = data[-2]
            data_flag = (top_byte >> 7) & 0x1
            handshake_state_rx = (top_byte >> 5) & 0x3
            transaction_id_rx = top_byte & 0x1f
            if ( data_flag ):
                if ( handshake_state_rx == 0x1 ):
                    # I sent out 0x0, got back 0x1, I won negotiation, need FIFO
                    self.state = dpof_single_channel.TRANSMIT_WON
                    return self.disable_decoder_if_time_over(), True
                elif ( handshake_state_rx == 0x0 ):
                    # I sent out 0x0, but also go back 0x0, negotiation
                    # check random numbers
                    if ( self.tx_rand_num < rand_id_rx ):
                        # I win, need FIFO now
                        self.state = dpof_single_channel.TRANSMIT_WON
                        return self.disable_decoder_if_time_over(), True
                    elif ( self.tx_rand_num > rand_id_rx ):
                        # I lose, but HOW DO I TRACK THE TX DATA?????
                        # for now whatever, query is discarded
                        self.state = dpof_single_channel.RX_SLAVE
                        self.stop_tx()
                    else:
                        # restart negotiation, just force start TX again
                        self.start_tx(self.tx_nego_state, 
                                self.current_transaction_id_tx,
                                self.fifo_to_send)

        # don't need fifo or decoder at this point
        return self.disable_decoder_if_time_over(), False

    def run_transmit_won_state(self):

        # don't proceed or do anything in this state without FIFO lock
        if ( not self.fifo_grant ):
            return self.disable_decoder_if_time_over(), True

        print(f"Transmit won state got fifo grant")
        if ( self.is_tx_query ): #I'm trying to query and have FIFO lock
            # enable PWM FIFO for reception
            # set fifo to receive, 0x0 for idle
            self.board.dpll.modules["PWM_USER_DATA"].write_reg(0,
                    "PWM_USER_DATA_PWM_USER_DATA_CMD_STS", 0x0)
           
            # send 0x1 to let other side know I'm ready to receive
            self.start_tx(0x1, self.current_transaction_id_tx, [])
            self.state = dpof_single_channel.TRANSMIT_QUERY
            # need to hold both decoder and FIFO
            print(f"Transmit won state, going to transmit query")
            return True, True
        else:
            # I'm trying to write to other end
            # Got 0x1, write to PWM FIFO and send it, wait for TX Completion on FIFO
            self.board.dpll.modules["PWM_USER_DATA"].write_reg(0,
                    "PWM_USER_DATA_PWM_USER_DATA_SIZE", len(self.fifo_to_send))

            self.board.dpll.modules["PWM_USER_DATA"].write_reg(0,
                    "PWM_USER_DATA_PWM_USER_DATA_CMD_STS", 0x1) #send transmission request
            self.state = dpof_single_channel.TRANSMIT_WRITE
            print(f"Transmit won state, going to transmit write")

        
        # need FIFO 
        return self.disable_decoder_if_time_over(), True

    def run_transmit_write_state(self):
        # assume fifo grant, without it wont get to this state
        # pseudo wait state, waiting for CMD_STS to 
        pwm_status = self.board.dpll.modules["PWM_USER_DATA"].read_reg(0,
                "PWM_USER_DATA_PWM_USER_DATA_CMD_STS")
        if ( pwm_status == 0x3 ): #got tx ack, can send data now
            for i in range(len(self.fifo_to_send)):
                reg_name = f"BYTE_OTP_EEPROM_PWM_BUFF_{i}"
                self.board.dpll.modules["EEPROM_DATA"].write_reg(0,
                        reg_name, self.fifo_to_send[i] )

            # start transmission
            self.board.dpll.modules["PWM_USER_DATA"].write_reg(0,
                    "PWM_USER_DATA_PWM_USER_DATA_CMD_STS", 0x2) #sent transmission request
    
            # don't necessarily need to wait for TX FIFO to say it's done, just wait for other side
            self.state = dpof_single_channel.TRANSMIT_DONE_WAIT

        return self.disable_decoder_if_time_over(), True

    def run_transmit_query_state(self):
        # assume fifo grant
        # wait for CMD_STS to say something about receiver
        # wait for PWM FIFO to fill up 
        fifo_status = self.board.dpll.modules["PWM_USER_DATA"].read_reg(0,
                "PWM_USER_DATA_PWM_USER_DATA_CMD_STS")
        print(f"Run transmit query state, fifo status {fifo_status:02x}")

        if ( fifo_status == 0xb ):

            print(f"PWM User data reception successful!")


            fifo_byte_count = self.board.dpll.modules["PWM_USER_DATA"].read_reg(0,
                    "PWM_USER_DATA_PWM_USER_DATA_SIZE")
            print(f"Received {fifo_byte_count} through FIFO")

            fifo_data = []

            for i in range(fifo_byte_count):
                reg_name = f"BYTE_OTP_EEPROM_PWM_BUFF_{i}"
                fifo_data.append( self.board.dpll.modules["EEPROM_DATA"].read_reg(0,
                        reg_name ) )

            print(f"Received fifo data: {fifo_data}") 

            # check if RX already sent 0x2, if not go to wait state
            data = self.check_decoder_new_data() 
            if ( len(data) == 0 ):
                # Case 1: Decoder inactive
                return self.disable_decoder_if_time_over(), False
            else:
                # Case 2: Decoder active
                # check decoder value
                top_byte = data[-1]
                rand_id_rx = data[-2]
                data_flag = (top_byte >> 7) & 0x1
                handshake_state_rx = (top_byte >> 5) & 0x3
                transaction_id_rx = top_byte & 0x1f
                if ( handshake_state_rx == 0x2 ): # got ack from other side
                    print(f"Transmit query state got 0x2, done")
                    self.stop_tx()
                    self.state = dpof_single_channel.IDLE
                    return self.disable_decoder_if_time_over(), False
                

            # go to this state to wait for 0x2
            self.state = dpof_single_channel.TRANSMIT_DONE_WAIT_STATE
            return self.disable_decoder_if_time_over(), False
        else:
            print(f"PWM User data reception not done yet! {fifo_status:02x}")

        # need to hold decoder and fifo
        return True, True

    def run_transmit_done_wait_state(self):
        # debug, read cmd status
        fifo_status = self.board.dpll.modules["PWM_USER_DATA"].read_reg(0,
                "PWM_USER_DATA_PWM_USER_DATA_CMD_STS")
        print(f"Run transmit done wait state debug fifo-status {fifo_status:02x}")

        # need to wait for rx data
        data = self.check_decoder_new_data() 
        if ( len(data) == 0 ):
            # Case 1: Decoder inactive
            return self.disable_decoder_if_time_over(), False
        else:
            # Case 2: Decoder active
            # check decoder value
            top_byte = data[-1]
            rand_id_rx = data[-2]
            data_flag = (top_byte >> 7) & 0x1
            handshake_state_rx = (top_byte >> 5) & 0x3
            transaction_id_rx = top_byte & 0x1f
            if ( handshake_state_rx == 0x2 ): # got ack from other side
                print(f"Transmit done wait state got 0x2, done")
                self.stop_tx()
                self.state = dpof_single_channel.IDLE

        return self.disable_decoder_if_time_over(), False

    # returns [bool, bool] value
    # First bool, whether decoder is now disabled or enabled, True for enabled
    # Second bool, if PWM FIFO is needed
    def top_state_machine(self):
        print(f"Board {self.board.board_num} top state machine channel {self.tod_num}")
        if ( self.state == dpof_single_channel.IDLE ):
            print(f"Running idle state")
            return self.run_idle_state()
        elif ( self.state == dpof_single_channel.RX_SLAVE ):
            print(f"Running rx slave state")
            return self.run_rx_slave_state()
        elif ( self.state == dpof_single_channel.RX_SLAVE_RESPOND_QUERY ):
            print(f"Running run_rx_slave_respond_query")
            return self.run_rx_slave_respond_query()
        elif ( self.state == dpof_single_channel.RX_SLAVE_RESPOND_QUERY_WAIT_FIFO_TX ):
            print(f"Running run_rx_slave_respond_query_wait_fifo_tx")
            return self.run_rx_slave_respond_query_wait_fifo_tx()
        elif ( self.state == dpof_single_channel.RX_SLAVE_RESPOND_QUERY_WAIT_FIFO_TX_DONE ):
            print(f"Running run_rx_slave_respond_query_wait_fifo_tx_done")
            return self.run_rx_slave_respond_query_wait_fifo_tx_done()
        elif ( self.state == dpof_single_channel.RX_SLAVE_WAIT_WRITE ):
            print(f"Running run_rx_slave_wait_write")
            return self.run_rx_slave_wait_write()
        elif ( self.state == dpof_single_channel.RX_SLAVE_DONE_WAIT ):
            print(f"Running run_rx_slave_done_wait")
            return self.run_rx_slave_done_wait()
        elif ( self.state == dpof_single_channel.TRANSMIT_START ):
            print(f"Running transmit start state")
            return self.run_transmit_start_state()
        elif ( self.state == dpof_single_channel.TRANSMIT_WON ):
            print(f"Running transmit won state")
            return self.run_transmit_won_state()
        elif ( self.state == dpof_single_channel.TRANSMIT_WRITE ):
            print(f"Running transmit write state")
            return self.run_transmit_write_state()
        elif ( self.state == dpof_single_channel.TRANSMIT_QUERY ):
            print(f"Running transmit query state")
            return self.run_transmit_query_state()
        elif ( self.state == dpof_single_channel.TRANSMIT_DONE_WAIT ):
            print(f"Running transmit done wait state")
            return self.run_transmit_done_wait_state()
        return self.disable_decoder_if_time_over(), False





################ TX FUNCTIONS ####################
    def can_tx(self):
        if ( self.state == dpof_single_channel.IDLE ):
            return True
        return False

    def start_tx(self, handshake_id=0, transaction_id = 0, fifo_data=[]):
        if ( self.tx_enabled == True ): # already enabled
            self.stop_tx()


        self.tx_rand_num = random.randint(0,255)
        tod_sec_top = (1<<7) + ((handshake_id & 0x3) << 5) + (transaction_id & 0x1f)
        tod_sec_bot = self.tx_rand_num
        tod_to_jump = (tod_sec_top << (8*5)) + (tod_sec_bot << (8*4))
        # relative jump positive
        self.board.write_tod_relative(self.encoder, 0, 0, tod_to_jump, True)
        self.tx_enabled = True
        self.time_tx_enabled = time.time()
        self.tx_nego_state = handshake_id
        self.current_transaction_id_tx = transaction_id
        self.fifo_to_send = fifo_data
        self.is_tx_query = self.is_transaction_id_query(transaction_id)
        self.state = dpof_single_channel.TRANSMIT_START

        print(f"Board {self.board.board_num} transmit start id={handshake_id} trans_id={transaction_id} data {fifo_data}")
        return True

    def stop_tx(self):
        if ( not self.tx_enabled ):
            return

        # read back current TOD
        cur_tod = self.read_current_tx_tod_seconds()

        # flip the order
        cur_tod.reverse()

        if ( cur_tod[0] & 0x80 ): # data bit is set , I need to clear it
            cur_tod_to_sub = (cur_tod[0] << (8*5)) + (cur_tod[1] << (8*4))
            # relative jump negative
            self.board.write_tod_relative(self.encoder, 0, 0, cur_tod_to_sub, False)
        self.tx_enabled = False 
        self.tx_nego_state = 0

################ RX FUNCTIONS ###################

    def is_transaction_id_query(self, query_id):
        #print(f"RX Transaction ID query!")
        if ( query_id == 0 ):
            return True
        return False

    # when I lose handshake, and far side is querying me
    def get_fifo_respond_to_query(self, query_id):
        print(f"RX Fill fifo respond to query {query_id}")
        if ( query_id == 0 ):
            return [0x1, 0x2, 0x3, 0x4]
            # big query
        else:
            pass
            # undefined
        return []

    # returns False if decoder disabled, true if still enabled 
    def disable_decoder_if_time_over(self):
        if ( self.get_how_long_decoder_on() >= self.decoder_time_slice_sec ):
            print(f"Stopping RX, {self.get_how_long_decoder_on()}, {self.decoder_time_slice_sec}")
            # time slice elapsed
            self.stop_rx()
            return False
        elif ( not self.rx_enabled ):
            return False
        return True


    def stop_rx(self):
        print(f"Board {self.board.board_num} stop rx {self.decoder}")
        # simple, turn off decoder
        self.board.dpll.modules["PWMDecoder"].write_field(self.decoder,
              "PWM_DECODER_CMD", "ENABLE", 0)
        self.rx_enable = False

    def start_rx(self):
        print(f"Board {self.board.board_num} start rx {self.decoder}")
        # record what PWM TOD is to detect change after enabling decoder
        self.pwm_tod_before_start_rx = self.read_raw_hardware_buffer()
        
        #enable decoder with frame access
        self.board.dpll.modules["PWMDecoder"].write_field(self.decoder,
              "PWM_DECODER_CMD", "ENABLE", 0x5)

        # record time when started decoder
        self.start_time_on_this_decoder = time.time()
        self.rx_enabled = True

    def get_how_long_decoder_on(self):
        if ( self.rx_enabled ):
            return time.time() - self.start_time_on_this_decoder
        return 0


    def check_decoder_new_data(self):
        if ( self.rx_enabled ):
            data = self.read_raw_hardware_buffer() 
            # only keep handshake bytes, top two bytes
            if ( (data == self.pwm_tod_before_start_rx) or
                    (data == self.last_data_this_decoder) ):
                # nothing new
                return []
            else:
                hex_val = [hex(val) for val in data]
                print(f" Debug check decoder new data {data} {self.last_data_this_decoder}")
                print(f"Board {self.board.board_num} decoder {self.decoder} new data {hex_val}")
                self.last_data_this_decoder = list(data)
                print(f" Debug {self.last_data_this_decoder}")
                return data
        return []


    # read current TOD encoder value, only two handshake bytes
    def read_current_tx_tod_seconds(self):
        # use read primary
        self.board.dpll.modules["TODReadPrimary"].write_reg(self.tod_num,
                "TOD_READ_PRIMARY_CMD", 0x0)
        self.board.dpll.modules["TODReadPrimary"].write_reg(self.tod_num,
                "TOD_READ_PRIMARY_CMD", 0x1)

        cur_tod = self.board.dpll.modules["TODReadPrimary"].read_reg_mul(self.tod_num,
                "TOD_READ_PRIMARY_SECONDS_32_39", 2)

        return cur_tod 

    # returns data from hardware buffer, only two handshake bytes 
    def read_raw_hardware_buffer(self):
        """ Read data from the hardware's global receive buffer. """
        # just read seconds portion
        data = self.board.i2c.read_dpll_reg_multiple(0xce80, 0x0, 11)
        hex_val = [hex(val) for val in data]
        print(f"Read PWM Raw Receive Board {self.board.board_num} TOD {hex_val}")
        return data[-2:]


class DPOF_Top():
    def __init__(self, board):
        self.board = board


        # disable all decoders
        for i in range(len(self.board.dpll.modules["PWMDecoder"].BASE_ADDRESSES)):
            self.board.dpll.modules["PWMDecoder"].write_field(i,
                  "PWM_DECODER_CMD", "ENABLE", 0)

        ######## RX Logic variables
        self.active_decoder = 0
        self.fifo_lock_chan = -1

        ##################### HUGE HACK, ONLY USE CHANNEL 0 FOR BENCHTOP DEBUG
        self.channels = [dpof_single_channel(self.board, i, i, i*2, 5) for i in range(1) ] # decoders skip one

        # enable one
        self.channels[self.active_decoder].start_rx()

        ####### TX Logic variables
        


    # top level function
    def tick(self): 
        for index, chan in enumerate(self.channels):
            print(f"Board {self.board.board_num} DPOF tick, chan {index}")
            decoder_enabled, fifo_needed = chan.top_state_machine()
            print(f"Board {self.board.board_num} DPOF tick, chan {index}, {decoder_enabled} , {fifo_needed}")


            if ( not decoder_enabled ): # it turned itself off
                if (index == self.active_decoder): # it was enabled
                    # enable next one
                    self.active_decoder = (self.active_decoder +1) % len(self.channels)
                    print(f"Board {self.board.board_num} switch to decoder {self.active_decoder}")
                    self.channels[self.active_decoder].start_rx()

            if ( fifo_needed ): #it's requesting FIFO control
                if ( self.fifo_lock_chan == -1):
                    chan.grant_fifo_control()
                    self.fifo_lock_chan = index

            if ( not fifo_needed ): #it doesn't need FIFO
                if ( self.fifo_lock_chan == index ): # it had fifo control
                    # release fifo lock
                    chan.release_fifo_control()
                    self.fifo_lock_chan = -1
                    


    def dpof_query(self, channel_num=0, query_id=0):
        if ( self.channels[channel_num].can_tx() ):
            print(f"Board {self.board.board_num} can TX, starting TX chan={channel_num} query_id={query_id}")
            self.channels[channel_num].start_tx(transaction_id = query_id)
            return True
        return False

    def dpof_write(self, channel_num=0, write_id=0, data=[]):
        if ( self.channels[channel_num].can_tx() ):
            self.channels[channel_num].start_tx(transaction_id=write_id,
                    fifo_data = data)
            return True
        return False


    
