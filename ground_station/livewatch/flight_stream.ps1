# Flight-test stream: CSV to logs/vofa/<Name>.slotN.csv, slot 0 forwarded to VOFA+ (UDP 127.0.0.1:1347).
# Usage (from repo root):  .\ground_station\livewatch\flight_stream.ps1 flight2
# Stop with Ctrl+C; CSV files are closed on exit.
param([Parameter(Mandatory = $true)][string]$Name)

Set-Location (Join-Path $PSScriptRoot '..\..')
python -m ground_station.livewatch.stream_log `
  --group "50:Ctrler.gyroxPID.FB,Ctrler.gyroyPID.FB,Ctrler.gyrozPID.FB,Ctrler.rollPID.FB,Ctrler.pitchPID.FB,Ctrler.yawPID.FB,Ctrler.locxPID.FB,Ctrler.locyPID.FB,Ctrler.gyrozPID.U,g_of_handheld_test,g_gyro_z_bias_blocks,Ctrler.locxPID.Des,Ctrler.locyPID.Des,ano_of.of_alt_cm,mymotor.motor1,mymotor.motor2,mymotor.motor3,mymotor.motor4,flight_phase,DroneStatus.ARM_Status,real_voltage" `
  --group "25:Ctrler.gyroxPID.Des,Ctrler.gyroxPID.U,Ctrler.gyroyPID.Des,Ctrler.gyroyPID.U,Ctrler.gyrozPID.Des,Ctrler.rollPID.Des,Ctrler.rollPID.U,Ctrler.pitchPID.Des,Ctrler.pitchPID.U,Ctrler.yawPID.Des,Ctrler.yawPID.U,Ctrler.locxPID.U,Ctrler.locyPID.U,Ctrler.Z_posPID.Des,Ctrler.Z_posPID.FB,Ctrler.Z_posPID.U,Ctrler.Z_ratePID.Des,Ctrler.Z_ratePID.FB,Ctrler.Z_ratePID.U" `
  --group "10:s_of_bias_x,s_of_bias_y,ano_of.of2_dx_fix,ano_of.of2_dy_fix,ano_of.of_quality,Gyro_Z_Offset,imu_data.rol,imu_data.pit,imu_data.yaw,g_of_bias_mode,dbg_motor_manual,TWC.execute" `
  --seconds 7200 --out "logs/vofa/$Name.csv" --vofa 127.0.0.1:1347
