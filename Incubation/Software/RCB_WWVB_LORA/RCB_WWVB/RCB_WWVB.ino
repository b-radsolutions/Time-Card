


#include "SI5341B.h"
#include "WWVB_Arduino.h"
#include "SiTime.h"
#include "SX1276_LORA.h"
#include "SX1257.h"
//#include <arm_math.h> // Arduino CMSIS DSP library for signal processing 
#include "mbed.h"


// the setup function runs once when you press reset or power the board

LoRaClass SX1276_Lora;

SX1257Class SX1257_SDR;

void SDR_Test_init();

void setup() {
  
  // Using STM32 HAL in general
  HAL_Init();

  __HAL_RCC_SYSCFG_CLK_ENABLE();

  // STM32 GPIO HAL, https://github.com/STMicroelectronics/stm32h7xx_hal_driver/blob/master/Src/stm32h7xx_hal_gpio.c
  __HAL_RCC_GPIOA_CLK_ENABLE();
  __HAL_RCC_GPIOB_CLK_ENABLE();
  __HAL_RCC_GPIOC_CLK_ENABLE();
  __HAL_RCC_GPIOD_CLK_ENABLE();
  __HAL_RCC_GPIOF_CLK_ENABLE();
  __HAL_RCC_GPIOG_CLK_ENABLE();
  __HAL_RCC_GPIOI_CLK_ENABLE();
  __HAL_RCC_GPIOJ_CLK_ENABLE(); // lots of GPIO on J
  __HAL_RCC_GPIOK_CLK_ENABLE(); // change K if using other GPIOs, but LED are all K
  

  

  // SPI5 for SX1276
  __HAL_RCC_SPI5_CLK_ENABLE();

  // SPI1 / 2 / 6 for SX1257
  __HAL_RCC_SPI1_CLK_ENABLE();
  __HAL_RCC_SPI2_CLK_ENABLE();
  __HAL_RCC_SPI6_CLK_ENABLE();

  wwvb_gpio_pinmode(WLED_RED, OUTPUT);
  wwvb_gpio_pinmode(WLED_GREEN, OUTPUT);
  wwvb_gpio_pinmode(WLED_BLUE, OUTPUT);

  wwvb_gpio_pinmode(WWVB_AMP1_CS, OUTPUT);
  wwvb_gpio_pinmode(WWVB_AMP2_CS, OUTPUT);

  wwvb_digital_write(WWVB_AMP1_CS, 1);
  wwvb_digital_write(WWVB_AMP2_CS, 1);
  
  Serial.begin(9600);
  while ( !Serial ) {
    delay(1);
  }
  init_sitime(10e6);
  init_si5341b();

  Serial.println("");
  Serial.println("");
  Serial.println("");


  /****** INTERRUPT IS DEFINITELY NOT RIGHT FOR PROPER LORA OPERATION IN SX1276_LORA ***/
  // SX1276 can use external interrupt
  
  SX1276_Lora.init(); 
  Serial.println("Beginning SX1276 LORA");
  if ( !SX1276_Lora.begin(915e6) ) {
    Serial.println("LoRA SX1276 init failed!");
  } else {
    Serial.println("LoRA SX1276 init successful!");
    SX1276_Lora.dumpRegisters(Serial);
  }
  

  SX1257_SDR.init();
  SX1257_SDR.set_tx_freq(900e6);
  SX1257_SDR.set_rx_freq(900e6);

  SX1257_SDR.dumpRegisters(Serial);

}

int lora_test_counter = 0;
void LoRA_Test() {
  Serial.print("Lora test start ");
  Serial.println(lora_test_counter);
  SX1276_Lora.setantenna(1, 1, 1); // high frequency SMA TX
  SX1276_Lora.beginPacket();
  SX1276_Lora.print("hello ");
  SX1276_Lora.print(lora_test_counter++);
  //SX1276_Lora.dumpRegisters(Serial);
  SX1276_Lora.endPacket(false); // run in sync mode, poll for TX complete
}

void SDR_Test_init() {
  SX1257_SDR.set_antenna(1); // setup antenna  for TX
  SX1276_Lora.setantenna(1, 1, 0); // high frequency SMA RX on standard transceiver
  SX1257_SDR.set_tx_mode(1, 1); // start transmitter

  SX1257_SDR.dumpRegisters(Serial);
}

bool first_SDR_test = 1;
void SDR_Test() {
  if ( first_SDR_test ) {
    SDR_Test_init();
    first_SDR_test = 0;
  }

  SX1257_SDR.write_I(0xff);
  SX1257_SDR.write_Q(0x0);

}

unsigned long last_led_toggle_millis = 0;
bool leds_on = 0;
int led_count = 0;

void led_loop() {
  if ( millis() - last_led_toggle_millis >= 1000 ) {
	  Serial.println("LED Loop start");
    if ( leds_on ) {      
      wwvb_digital_write(WLED_RED, HIGH);
      wwvb_digital_write(WLED_BLUE, HIGH);
      wwvb_digital_write(WLED_GREEN, HIGH);
      leds_on = 0;
    } else {      
      if ( led_count == 0 ) {
        wwvb_digital_write(WLED_RED, LOW);
        wwvb_digital_write(WLED_BLUE, HIGH);
        wwvb_digital_write(WLED_GREEN, HIGH);
        led_count++;
      } 
      else if ( led_count == 1 ) {
        wwvb_digital_write(WLED_RED, HIGH);
        wwvb_digital_write(WLED_BLUE, LOW);
        wwvb_digital_write(WLED_GREEN, HIGH);
        led_count++;
      }
      else if ( led_count == 2 ) {
        wwvb_digital_write(WLED_RED, HIGH);
        wwvb_digital_write(WLED_BLUE, HIGH);
        wwvb_digital_write(WLED_GREEN, LOW);
        led_count = 0;
      }
      leds_on = 1;
    }
    last_led_toggle_millis = millis();
    //SX1257_SDR.dumpRegisters(Serial);
    //SDR_Test();
    
  } 

}



// the loop function runs over and over again forever
void loop() {

  led_loop();
  //LoRA_Test();
  //SDR_Test(); 


}
