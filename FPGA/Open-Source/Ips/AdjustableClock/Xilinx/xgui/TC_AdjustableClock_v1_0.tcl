# Definitional proc to organize widgets for parameters.
proc init_gui { IPINST } {
  ipgui::add_param $IPINST -name "Component_Name"
  #Adding Page
  set Page_0 [ipgui::add_page $IPINST -name "Page 0"]
  ipgui::add_param $IPINST -name "ClockInHoldoverTimeoutSecond_Gen" -parent ${Page_0}
  ipgui::add_param $IPINST -name "ClockInSyncThreshold_Gen" -parent ${Page_0}
  ipgui::add_param $IPINST -name "ClockPeriod_Gen" -parent ${Page_0}


}

proc update_PARAM_VALUE.ClockInHoldoverTimeoutSecond_Gen { PARAM_VALUE.ClockInHoldoverTimeoutSecond_Gen } {
	# Procedure called to update ClockInHoldoverTimeoutSecond_Gen when any of the dependent parameters in the arguments change
}

proc validate_PARAM_VALUE.ClockInHoldoverTimeoutSecond_Gen { PARAM_VALUE.ClockInHoldoverTimeoutSecond_Gen } {
	# Procedure called to validate ClockInHoldoverTimeoutSecond_Gen
	return true
}

proc update_PARAM_VALUE.ClockInSyncThreshold_Gen { PARAM_VALUE.ClockInSyncThreshold_Gen } {
	# Procedure called to update ClockInSyncThreshold_Gen when any of the dependent parameters in the arguments change
}

proc validate_PARAM_VALUE.ClockInSyncThreshold_Gen { PARAM_VALUE.ClockInSyncThreshold_Gen } {
	# Procedure called to validate ClockInSyncThreshold_Gen
	return true
}

proc update_PARAM_VALUE.ClockPeriod_Gen { PARAM_VALUE.ClockPeriod_Gen } {
	# Procedure called to update ClockPeriod_Gen when any of the dependent parameters in the arguments change
}

proc validate_PARAM_VALUE.ClockPeriod_Gen { PARAM_VALUE.ClockPeriod_Gen } {
	# Procedure called to validate ClockPeriod_Gen
	return true
}


proc update_MODELPARAM_VALUE.ClockPeriod_Gen { MODELPARAM_VALUE.ClockPeriod_Gen PARAM_VALUE.ClockPeriod_Gen } {
	# Procedure called to set VHDL generic/Verilog parameter value(s) based on TCL parameter value
	set_property value [get_property value ${PARAM_VALUE.ClockPeriod_Gen}] ${MODELPARAM_VALUE.ClockPeriod_Gen}
}

proc update_MODELPARAM_VALUE.ClockInSyncThreshold_Gen { MODELPARAM_VALUE.ClockInSyncThreshold_Gen PARAM_VALUE.ClockInSyncThreshold_Gen } {
	# Procedure called to set VHDL generic/Verilog parameter value(s) based on TCL parameter value
	set_property value [get_property value ${PARAM_VALUE.ClockInSyncThreshold_Gen}] ${MODELPARAM_VALUE.ClockInSyncThreshold_Gen}
}

proc update_MODELPARAM_VALUE.ClockInHoldoverTimeoutSecond_Gen { MODELPARAM_VALUE.ClockInHoldoverTimeoutSecond_Gen PARAM_VALUE.ClockInHoldoverTimeoutSecond_Gen } {
	# Procedure called to set VHDL generic/Verilog parameter value(s) based on TCL parameter value
	set_property value [get_property value ${PARAM_VALUE.ClockInHoldoverTimeoutSecond_Gen}] ${MODELPARAM_VALUE.ClockInHoldoverTimeoutSecond_Gen}
}

