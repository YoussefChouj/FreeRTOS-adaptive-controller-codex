# Generated platform registry

Registry version: 1
Schema version: 1
Registry CRC32: `0x9F32E2EA`

| Kind | ID | Name | Type | Unit | Owner | Rate | Permissions | Safety | Version |
|---|---:|---|---|---|---|---:|---|---|---:|
| variable | 1 | `build_id` | u32_array | none | build | 0 | read | observation | 1 |
| variable | 2 | `DroneStatus.ARM_Status` | u8 | enum | flight_fsm | 200 | read | critical | 1 |
| variable | 3 | `DroneStatus.FlyMode` | u8 | enum | flight_fsm | 100 | read | critical | 1 |
| variable | 4 | `g_telemetry_mode` | u8 | enum | telemetry | 80 | read | operational | 1 |
| variable | 5 | `gs_cmd_drop_count` | u32 | count | command_ingress | 80 | read | diagnostic | 1 |
| enum | 1 | `telemetry_mode` | enum_u8 | none | telemetry | 0 | read | operational | 1 |
| enum | 2 | `safety_class` | enum_u8 | none | platform | 0 | read | observation | 1 |
| parameter | 1 | `gs_max_horizontal_speed_mps` | f32 | m/s | safety | 0 | read_write | critical | 1 |
| parameter | 2 | `gs_max_vertical_speed_mps` | f32 | m/s | safety | 0 | read_write | critical | 1 |
| parameter | 3 | `gs_max_pitch_deg` | f32 | deg | safety | 0 | read_write | critical | 1 |
| parameter | 4 | `gs_max_roll_deg` | f32 | deg | safety | 0 | read_write | critical | 1 |
| command | 1 | `pid_gain` | f32 | gain | control | 0 | execute | boundary | 1 |
| command | 4 | `flight_mode` | f32 | enum | flight_fsm | 0 | execute | critical | 1 |
| command | 6 | `virtual_rc` | f32 | normalized | rc_input | 100 | execute | critical | 1 |
| command | 13 | `abort_all` | f32 | boolean | safety | 0 | execute | critical | 1 |
| command | 14 | `sdk_arm_authority` | f32 | boolean | flight_fsm | 0 | execute | critical | 1 |
| command | 20 | `sysid` | f32 | mixed | experiment | 0 | execute | critical | 1 |
| command | 22 | `motor_bench` | f32 | mixed | bench | 0 | execute | critical | 1 |
| telemetry | 1 | `legacy_status` | frame_0x01 | mixed | telemetry | 100 | read | observation | 14 |
| telemetry | 2 | `legacy_adaptive` | frame_0x02 | mixed | telemetry | 20 | read | observation | 14 |
| telemetry | 6 | `extended_attitude` | frame_0x06 | mixed | telemetry | 50 | read | observation | 14 |
| telemetry | 9 | `typed_stream` | frame_0x09 | mixed | subscribe | 80 | read | observation | 1 |
| event | 1 | `boot` | event | none | platform | 0 | read | diagnostic | 1 |
| event | 2 | `command_outcome` | event | none | command_service | 0 | read | critical | 1 |
| plugin | 1 | `pid_controller` | controller | none | control | 200 | read | critical | 1 |
| plugin | 2 | `mrac_adaptive_layer` | adaptive | none | control | 200 | read | critical | 1 |
| plugin | 3 | `ekf9_estimator` | estimator | none | estimation | 80 | read | critical | 1 |
| plugin | 4 | `optical_flow` | estimator | none | estimation | 200 | read | operational | 1 |
| task | 1 | `SystemMonitor_Task` | freertos_task | none | scheduler | 1 | read | diagnostic | 1 |
| task | 2 | `IMU_DataDeal_Task` | freertos_task | none | scheduler | 1000 | read | critical | 1 |
| task | 3 | `IMUSample_Task` | freertos_task | none | scheduler | 1000 | read | critical | 1 |
| task | 4 | `Stabilizer_Task` | freertos_task | none | scheduler | 200 | read | critical | 1 |
| task | 5 | `Remoter_Task` | freertos_task | none | scheduler | 100 | read | critical | 1 |
| task | 6 | `Autofly_Task` | freertos_task | none | scheduler | 200 | read | critical | 1 |
| task | 7 | `Send_Task` | freertos_task | none | scheduler | 80 | read | operational | 1 |
| resource | 1 | `usart3_radio` | uart_dma | baud | bsp | 0 | read | operational | 1 |
| resource | 2 | `uart5_probe_vcp` | uart_dma | baud | bsp | 0 | read | diagnostic | 1 |
| resource | 3 | `command_queue` | ring | entries | command_ingress | 80 | read | operational | 1 |
| resource | 4 | `freertos_heap` | heap | bytes | scheduler | 0 | read | critical | 1 |
