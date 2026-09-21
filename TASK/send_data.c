#pragma diag_suppress 1267

#include "send_data.h"
#include "mrac.h"
#include "gyro_filter.h"
#include "sysid.h"
#include "pid.h"
#include "robot_types.h"
#include "global_declare.h"
#include "rc_input.h"
#include "flight_fsm.h"
#include "Ano_OF.h"
#include "rpm.h"
#include "FreeRTOS.h"
#include "task.h"
#include "imu_update.h"  /* imu_data, Lin_Acc_X/Y_body */
#include "calib.h"       /* CalTrim_t, CalHot_t */
#include "ekf.h"         /* Ekf9_t, ADR-0011 9-state EKF */
#include "usart5.h"      /* UART5 extended-prefix subscribe hook (uart5_address_subscription_cmd) */
#include "subscribe.h"   /* Subscribe_StreamTick / Subscribe_StreamOwnsUsart3 */
#include "gs_command.h"
#include "command_protocol.h"
#include "rtos_observability.h"
#include "send_prof.h"

/* Body-frame gyroscope rates (rad/s) — needed for Frame C body-rate telemetry.
 * Declared as extern in bmi088_driver.h. */
extern FP32 Gyro_X_Real;
extern FP32 Gyro_Y_Real;
extern FP32 Gyro_Z_Real;

/* ADR-0011: calibration state from TASK/StabilizerTask.c */
extern volatile uint8_t g_of_bias_capture_req; /* CMD 0x17 one-shot OF bias capture req */
extern volatile uint8_t g_of_bias_mode;        /* CMD 0x1E idx=0: 0=FIXED 1=EMA 2=EKF */
extern volatile uint8_t g_of_bias_ema_freeze;  /* CMD 0x1E idx=1: 1=freeze EMA update */
extern float g_of_bias_ema_tau_s;              /* CMD 0x1E idx=2: EMA tau (s), [1,300] */
extern volatile uint8_t g_ekf_of_health;       /* 1=EKF healthy, 0=diverged (fallback active) */
extern volatile uint8_t g_ekf_of_fallback;     /* 1=EKF fell back to FIXED this flight */
extern CalTrim_t s_cal_trim;   /* accel bias (mg), from TASK/StabilizerTask.c */
extern CalHot_t  s_cal_hot;    /* gyro bias (rad/s), from TASK/StabilizerTask.c */
extern uint16_t  g_cal_health; /* bitmask, from TASK/StabilizerTask.c */

/* ADR-0011: estimator readiness from API/imu_update.c */
extern uint8_t g_estimator_ready;

/* EKF run gate — 1 = the filter predict/update actually executes (s_ekf.active=1).
 * Observe the running state directly over SWD (ground_station/livewatch, no wire
 * bytes), so running is decoupled from telemetry emission below. */
#ifndef EKF_RUN_ENABLED
#define EKF_RUN_ENABLED 1
#endif

/* EKF telemetry gate — flip EKF_TELEM_ENABLED to 1 to emit EKF fields in the 0x05
 * frame (+20 bytes). Default 0: EKF may run (see EKF_RUN_ENABLED) but emits no extra
 * bytes, so the 0x05 layout stays a compatible contract with serial_bridge.py. */
#ifndef EKF_TELEM_ENABLED
#define EKF_TELEM_ENABLED 1
#endif

static Ekf9_t s_ekf;          /* ADR-0011 parallel EKF instance */
static uint8_t s_ekf_inited = 0U;

/**
 * @module  send_data.c
 * @subsystem  comm
 * @depends  send_data.h, mrac.h, pid.h, robot_types.h, global_declare.h
 * @owns  telemetry frame serialization and ground-station command dispatch
 * @caution  command and telemetry byte layouts are shared contracts with ground_station/comm/serial_bridge.py
 */

_linux_flag stm32_to_linux_flag;

/* CRC16-CCITT (XModem) — used for Frame C checksum. */
static uint16_t crc16_xmodem(const uint8_t* data, uint16_t len)
{
    uint16_t crc = 0x0000U;
    uint16_t i;
    while (len--) {
        crc ^= (uint16_t)(*data++) << 8;
        for (i = 0; i < 8; i++) {
            if (crc & 0x8000U) {
                crc = (crc << 1) ^ 0x1021U;
            } else {
                crc <<= 1;
            }
        }
    }
    return crc;
}

/* Frame C body-rate/attitude/position telemetry (0x06, 50 Hz).
 * Emitted back-to-back with Frame A inside the same DMA TX call so they are atomic on wire.
 * Layout (50 B payload): rol, pit, yaw, gyro[3], earth_x, earth_y, altitude, rpm[4], seq_hi, seq_lo.
 * CRC16 covers [frame_type | LEN_hi | LEN_lo | MAX_NUM_BASIS | payload bytes].
 * SerialBridge ignores 0x06 on firmware older than GS_PROTO_VERSION v13. */
static uint8_t  s_frame_c_buf[60] = {0};
static uint16_t s_frame_c_seq      = 0U;

/*************************************************************************
�� �� ����void ANO_Report_UserData1(void)
�������ܣ����ߴ��ڷ�������
��    ע��PA10(USART1_RX)
*************************************************************************/
void ANO_Report_UserData1(void)  //����5
{
	Get_Voltage();
	float senddata[16];

	senddata[0] =	 real_voltage ;
  senddata[1] =  Ctrler.Z_ratePID.U ; 
	senddata[2] =  Ctrler.locxPID.FB;    
	senddata[3] =  Ctrler.locyPID.FB ;
  senddata[4] =  Ctrler.Z_posPID.FB ; 
	senddata[5] =  imu_data.pit; 
	senddata[6] =  1;  
	senddata[7] =  8 ;  
	senddata[8] =  9;  
	senddata[9] = 10 ;  
	senddata[10]=  11 ;
	senddata[11] = 12;
	senddata[12] = 13 ;
	senddata[13] = 14;
	senddata[14] = 15;
	senddata[15] = 16 ;

	Custom_DataBuf[0]= BYTE0(senddata[0]) ;//   
	Custom_DataBuf[1]= BYTE1(senddata[0]) ;//   
	Custom_DataBuf[2]= BYTE2(senddata[0]) ;//    
	Custom_DataBuf[3]= BYTE3(senddata[0]) ;// 
	
	Custom_DataBuf[4]= BYTE0(senddata[1]) ;//    
	Custom_DataBuf[5]= BYTE1(senddata[1]) ;//    
	Custom_DataBuf[6]= BYTE2(senddata[1]) ;//   
	Custom_DataBuf[7]= BYTE3(senddata[1]) ;//  
	
	Custom_DataBuf[8]= BYTE0(senddata[2]) ;//    
	Custom_DataBuf[9]= BYTE1(senddata[2]) ;//    
	Custom_DataBuf[10]= BYTE2(senddata[2]) ;//    
	Custom_DataBuf[11]= BYTE3(senddata[2]) ;//   
	
	Custom_DataBuf[12]= BYTE0(senddata[3]) ;//   
	Custom_DataBuf[13]= BYTE1(senddata[3]) ;//   
	Custom_DataBuf[14]= BYTE2(senddata[3]) ;//    
	Custom_DataBuf[15]= BYTE3(senddata[3]) ;//  
	
	Custom_DataBuf[16]= BYTE0(senddata[4]) ;//   
	Custom_DataBuf[17]= BYTE1(senddata[4]) ;//   
	Custom_DataBuf[18]= BYTE2(senddata[4]) ;//    
	Custom_DataBuf[19]= BYTE3(senddata[4]) ;//  
	
	Custom_DataBuf[20]= BYTE0(senddata[5]) ;//   
	Custom_DataBuf[21]= BYTE1(senddata[5]) ;//   
	Custom_DataBuf[22]= BYTE2(senddata[5]) ;//    
	Custom_DataBuf[23]= BYTE3(senddata[5]) ;//  
	
	Custom_DataBuf[24]= BYTE0(senddata[6]) ;//   
	Custom_DataBuf[25]= BYTE1(senddata[6]) ;//   
	Custom_DataBuf[26]= BYTE2(senddata[6]) ;//    
	Custom_DataBuf[27]= BYTE3(senddata[6]) ;//  
	
	Custom_DataBuf[28]= BYTE0(senddata[7]) ;//   
	Custom_DataBuf[29]= BYTE1(senddata[7]) ;//   
	Custom_DataBuf[30]= BYTE2(senddata[7]) ;//    
	Custom_DataBuf[31]= BYTE3(senddata[7]) ;//  
	
	Custom_DataBuf[32]= BYTE0(senddata[8]) ;//   
	Custom_DataBuf[33]= BYTE1(senddata[8]) ;//   
	Custom_DataBuf[34]= BYTE2(senddata[8]) ;//    
	Custom_DataBuf[35]= BYTE3(senddata[8]) ;// 
	
	Custom_DataBuf[36]= BYTE0(senddata[9]) ;//   
	Custom_DataBuf[37]= BYTE1(senddata[9]) ;//   
	Custom_DataBuf[38]= BYTE2(senddata[9]) ;//    
	Custom_DataBuf[39]= BYTE3(senddata[9]) ;// 
	
	Custom_DataBuf[40]= BYTE0(senddata[10]) ;//   
	Custom_DataBuf[41]= BYTE1(senddata[10]) ;//   
	Custom_DataBuf[42]= BYTE2(senddata[10]) ;//    
	Custom_DataBuf[43]= BYTE3(senddata[10]) ;// 
	
	Custom_DataBuf[44]= BYTE0(senddata[11]) ;//   
	Custom_DataBuf[45]= BYTE1(senddata[11]) ;//   
	Custom_DataBuf[46]= BYTE2(senddata[11]) ;//    
	Custom_DataBuf[47]= BYTE3(senddata[11]) ;// 
	
	Custom_DataBuf[48]= BYTE0(senddata[12]) ;//   
	Custom_DataBuf[49]= BYTE1(senddata[12]) ;//   
	Custom_DataBuf[50]= BYTE2(senddata[12]) ;//    
	Custom_DataBuf[51]= BYTE3(senddata[12]) ;// 
	
	Custom_DataBuf[52]= BYTE0(senddata[13]) ;//   
	Custom_DataBuf[53]= BYTE1(senddata[13]) ;//   
	Custom_DataBuf[54]= BYTE2(senddata[13]) ;//    
	Custom_DataBuf[55]= BYTE3(senddata[13]) ;// 
	
	Custom_DataBuf[56]= BYTE0(senddata[14]) ;//   
	Custom_DataBuf[57]= BYTE1(senddata[14]) ;//   
	Custom_DataBuf[58]= BYTE2(senddata[14]) ;//    
	Custom_DataBuf[59]= BYTE3(senddata[14]) ;// 
	
	Custom_DataBuf[60]= BYTE0(senddata[15]) ;//   
	Custom_DataBuf[61]= BYTE1(senddata[15]) ;//   
	Custom_DataBuf[62]= BYTE2(senddata[15]) ;//    
	Custom_DataBuf[63]= BYTE3(senddata[15]) ;// 
	
	Custom_DataBuf[64]=  0x00 ;//    
	Custom_DataBuf[65]=  0x00 ;//  
	Custom_DataBuf[66]=  0x80 ;//    
	Custom_DataBuf[67]=  0x7f ;// 
	
	/*--------------------------����DMA����---------------------------*/
	
  /* Non-blocking check: skip if previous DMA1_Stream7 transfer is still active */
  if (DMA_GetCurrDataCounter(DMA1_Stream7) != 0U) {
      return;
  }
  //��֮ǰ�ķ���
  DMA_ClearITPendingBit(DMA1_Stream7, DMA_IT_TCIF7); //����DMA_Mode_Normal,����û��ʹ������ж�ҲҪ�������������ֻ��һ��
    
  DMA_Cmd(DMA1_Stream7, DISABLE);				             //���õ�ǰ����ֵǰ�Ƚ���DMA
  DMA1_Stream7->M0AR = (uint32_t)&Custom_DataBuf;  //���õ�ǰ�������ݻ���ַ:Memory0 tARget
  DMA1_Stream7->NDTR = 68;     //���õ�ǰ���������ݵ�����:Number of Data units to be TRansferred
  DMA_Cmd(DMA1_Stream7, ENABLE);		
                                        //����DMA���� 
                                        //����DMA���� 		
}

UCHAR8 DataBuf_to_linux[52] = {0}; 
void send_to_linux(void)    //����4
{
	float senddata[13];

	//senddata[0]  ��Ϊ֡ͷ
	senddata[1] = real_voltage;
	senddata[2] = Ctrler.Z_ratePID.U; 
	senddata[3] =	3; 
  senddata[4] = 4; 
	senddata[5] = 5;
	
	senddata[6] = 6;
	senddata[7] = 7;
	senddata[8] = 8; 
	senddata[9] = 9; 
	senddata[10]= 10; 
	senddata[11]= 11;   //Ԥ��
	senddata[12]= 12;   //Ԥ��

	DataBuf_to_linux[0]= 0xAA ;  
	DataBuf_to_linux[1]= 0xAA ; 
	DataBuf_to_linux[2]= 0x00 ; 
	DataBuf_to_linux[3]= 0x00 ;
	
	DataBuf_to_linux[4]= BYTE0(senddata[1]) ;//    
	DataBuf_to_linux[5]= BYTE1(senddata[1]) ;//    
	DataBuf_to_linux[6]= BYTE2(senddata[1]) ;//   
	DataBuf_to_linux[7]= BYTE3(senddata[1]) ;//  
	
	DataBuf_to_linux[8]= BYTE0(senddata[2]) ;//    
	DataBuf_to_linux[9]= BYTE1(senddata[2]) ;//    
	DataBuf_to_linux[10]= BYTE2(senddata[2]) ;//    
	DataBuf_to_linux[11]= BYTE3(senddata[2]) ;//   
	
	DataBuf_to_linux[12]= BYTE0(senddata[3]) ;//   
	DataBuf_to_linux[13]= BYTE1(senddata[3]) ;//   
	DataBuf_to_linux[14]= BYTE2(senddata[3]) ;//    
	DataBuf_to_linux[15]= BYTE3(senddata[3]) ;//  
	
	DataBuf_to_linux[16]= BYTE0(senddata[4]) ;//   
	DataBuf_to_linux[17]= BYTE1(senddata[4]) ;//   
	DataBuf_to_linux[18]= BYTE2(senddata[4]) ;//    
	DataBuf_to_linux[19]= BYTE3(senddata[4]) ;//  
	
	DataBuf_to_linux[20]= BYTE0(senddata[5]) ;//   
	DataBuf_to_linux[21]= BYTE1(senddata[5]) ;//   
	DataBuf_to_linux[22]= BYTE2(senddata[5]) ;//    
	DataBuf_to_linux[23]= BYTE3(senddata[5]) ;//  
	
	DataBuf_to_linux[24]= BYTE0(senddata[6]) ;//   
	DataBuf_to_linux[25]= BYTE1(senddata[6]) ;//   
	DataBuf_to_linux[26]= BYTE2(senddata[6]) ;//    
	DataBuf_to_linux[27]= BYTE3(senddata[6]) ;//  
	
	DataBuf_to_linux[28]= BYTE0(senddata[7]) ;//   
	DataBuf_to_linux[29]= BYTE1(senddata[7]) ;//   
	DataBuf_to_linux[30]= BYTE2(senddata[7]) ;//    
	DataBuf_to_linux[31]= BYTE3(senddata[7]) ;//  
	
	DataBuf_to_linux[32]= BYTE0(senddata[8]) ;//   
	DataBuf_to_linux[33]= BYTE1(senddata[8]) ;//   
	DataBuf_to_linux[34]= BYTE2(senddata[8]) ;//    
	DataBuf_to_linux[35]= BYTE3(senddata[8]) ;// 
	
	DataBuf_to_linux[36]= BYTE0(senddata[9]) ;//   
	DataBuf_to_linux[37]= BYTE1(senddata[9]) ;//   
	DataBuf_to_linux[38]= BYTE2(senddata[9]) ;//    
	DataBuf_to_linux[39]= BYTE3(senddata[9]) ;// 
	
	DataBuf_to_linux[40]= BYTE0(senddata[10]) ;//    
	DataBuf_to_linux[41]= BYTE1(senddata[10]) ;//  
	DataBuf_to_linux[42]= BYTE2(senddata[10]) ;//    
	DataBuf_to_linux[43]= BYTE3(senddata[10]) ;// 
	
	DataBuf_to_linux[44]= BYTE0(senddata[11]) ;//    
	DataBuf_to_linux[45]= BYTE1(senddata[11]) ;//  
	DataBuf_to_linux[46]= BYTE2(senddata[11]) ;//    
	DataBuf_to_linux[47]= BYTE3(senddata[11]) ;// 
	
	DataBuf_to_linux[48]= BYTE0(senddata[12]) ;//    
	DataBuf_to_linux[49]= BYTE1(senddata[12]) ;//  
	DataBuf_to_linux[50]= BYTE2(senddata[12]) ;//    
	DataBuf_to_linux[51]= BYTE3(senddata[12]) ;// 
	
  /* UART4 was disabled 2026-07-21 (PA0/PA1 repurposed as RPM inputs — see BSP.c
   * / BSP/rpm.h). With UART4 unclocked, its TX DMA (DMA1_Stream4) never drains,
   * so the busy-wait below would spin forever and hang Send_Task — which halts
   * ALL ground-station telemetry (UART5). Skip the transfer while UART4 is
   * disabled; re-enabling UART4_Configuration() makes this path live again with
   * no further change. Root cause of the 2026-07-22 "connected but telemetry
   * stale" outage. */
  if ((UART4->CR1 & USART_CR1_UE) == 0U) {
      return;
  }

  /* Non-blocking check: skip if previous DMA1_Stream4 transfer is still active */
  if (DMA_GetCurrDataCounter(DMA1_Stream4) != 0U) {
      return;
  }
  //֮ǰķ
  DMA_ClearITPendingBit(DMA1_Stream4, DMA_IT_TCIF4); //DMA_Mode_Normal,ûʹжҲҪֻһ
    
  DMA_Cmd(DMA1_Stream4, DISABLE);				             //õǰֵǰȽDMA
  {
      uint32_t timeout = 1000U;
      while ((DMA_GetCmdStatus(DMA1_Stream4) == ENABLE) && (--timeout > 0U)) { }
  }
  DMA1_Stream4->M0AR = (uint32_t)&DataBuf_to_linux;  //õǰݻַ:Memory0 tARget
  DMA1_Stream4->NDTR = 52;     //õǰݵ:Number of Data units to be TRansferred
  DMA_Cmd(DMA1_Stream4, ENABLE);		
                                        //DMA 		

}
/* --- USART3 throughput test mode -- TEMPORARY, set back to 0 when done ------
 * Why this exists: the normal 16-byte VOFA+ attitude frame is content-limited to
 * ~1.3 kB/s, i.e. 11% of the 115200 wire, so no host-side measurement can ever
 * reveal what the 24RF radio actually carries. Measured 2026-07-27: at zero
 * offered load the downlink is 1284 B/s at 100% frame integrity, which says
 * nothing about capacity.
 *
 * Frame size is bounded by the DMA-busy guard below, NOT by the wire: a frame
 * must finish clocking out inside ONE Send_Task tick or the next tick skips it
 * and the rate halves. Measured 2026-07-27 with 112 B (9.72 ms vs a 10 ms
 * tick, only 0.28 ms of margin): it alternated, giving 48.3 Hz / 5413 B/s at
 * 0.14% air loss -- clean, but only 47% of the wire, and that 47% was our own
 * cadence artifact rather than the radio's limit.
 *
 * 23 floats = 96 B = 8.33 ms leaves 1.67 ms of margin, which should hold a true
 * 100 Hz = 9600 B/s = 83% of the 11520 B/s wire cap. If the radio delivers that
 * as cleanly as it delivered 5413 B/s, the radio is not the constraint at all
 * and the 115200 UART is the true ceiling.
 *
 * float[0] is a frame sequence counter so the host can count dropped frames
 * exactly rather than inferring loss from byte totals. Still JustFloat-framed
 * (N floats + 00 00 80 7F tail) and still strictly TX-only -- no command
 * dispatch is added on USART3.
 *
 * Affects only what is written to the USART3 radio, which carries no control
 * function: no s_ekf writes, no PID state, no arming/motor effect. UART5
 * telemetry and the EKF step are untouched. */
/* --- 2026-07-31, NEW BLE RADIO: the binding constraint moved off the UART -------
 * The 24RF pair above is retired. The replacement BLE module runs 921600 on both
 * ends (BSP/usart3.h), so the wire now carries 92160 B/s and the DMA-busy reasoning
 * above no longer bites: 63 floats = 256 B clocks out in 2.8 ms, well inside the
 * 10 ms tick.
 *
 * But the AIR link does not keep up. Measured 2026-07-31 with 63 floats at 100 Hz
 * (25600 B/s offered): only ~4700 B/s arrives, and framing never survives. The bytes
 * that do arrive are BIT-PERFECT -- the full 59-float ramp was recovered verbatim,
 * 92.5% of received words were ramp values, 256 distinct byte values -- so this is
 * NOT corruption. It is overflow: three consecutive captures at the same settings
 * gave a complete 59-float run, then zero, then two tails, which is what a module
 * dropping whatever it cannot carry looks like. The seller's "45 KB/s" air figure is
 * not what this pair delivers; ~4.7 kB/s is.
 *
 * CAPACITY IS NOT YET MEASURED, and neither number above is a measurement of it:
 * 36 B frames delivered 2888 B/s at 0.000% loss, but that is all the source OFFERED,
 * so it is only a lower bound; the ~4700 B/s came from deep overflow, where a module
 * discarding partial frames delivers LESS than it can carry, so that understates it.
 * The truth is somewhere in 2.9..20.5 kB/s and only intermediate loads separate them.
 *
 * Hence the LADDER: rather than one reflash per data point, the frame size steps
 * automatically through the rungs below, holding each for USART3_TEST_STEP_FRAMES so
 * the host sees a long clean stretch at every offered rate. One flash yields the whole
 * degradation curve. The top rung offers ~72 kB/s -- 1.6x the seller's 45 KB/s claim --
 * so the curve brackets that figure from ABOVE instead of assuming it.
 *
 * FIRST SWEEP DONE 2026-07-31 -- the coarse ladder {8,16,24,32,48,64,96,128,160,192,
 * 224} spanning 2.9..72 kB/s answered the headline question: the seller's 45 KB/s air
 * figure is fiction for this pair. Measured, at the 80.2 Hz cadence:
 *      8 floats  2887 B/s offered -> 2887 delivered, 0.00% loss, 100% intact
 *     16 floats  5454 offered     -> 4227 delivered, 17.87% loss, 94.3% intact
 *     24 floats  8020 offered     -> 2059 delivered, 59.24% loss
 *     32 floats 10586 offered     ->  235 delivered, 80.57% loss
 *     >=64 floats                 -> nothing survives at all
 * Note the SHAPE: past 16 floats, offering more DELIVERS LESS -- congestion collapse,
 * the module burning airtime on fragments. Capacity is ~4.2 kB/s, ~9% of the claim.
 * The limit is the module's BLE connection parameters (interval / packets per event),
 * which its Windows tool cannot change -- it exposes only baud, TX power and pairing.
 *
 * SECOND SWEEP pinned the knee: 12 floats = 52 B at 80.2 Hz = 4170 B/s, 0.00% loss over
 * 960 frames. 14 floats -> 7.85%, 16 -> 14.71%, 20 -> 42.38%.
 *
 * THIRD SWEEP (this one) -- BYTE-rate limited or PACKET-rate limited?
 * Every rung so far ran at the same 80.2 Hz and varied only frame size, so the two are
 * still confounded. This matters for real use: telemetry does NOT need to be real time,
 * so if the cap is bytes/s we can trade cadence for width and log far more variables
 * at once (variables ~ capacity / rate):
 *     80.2 Hz ->  52 B ->  12 floats      10 Hz -> 416 B -> 103 floats
 *     20   Hz -> 208 B ->  51 floats       5 Hz -> 832 B -> 207 floats
 * That trade only holds if a big slow frame survives as well as a small fast one.
 *
 * So this ladder is ISO-BYTE-RATE: frame size and cadence move inversely, holding the
 * offered rate at the known-good 4170 B/s. A divider of N means "emit on every Nth
 * Send_Task tick".
 *     floats div    Hz     frame B   offered B/s
 *        12   1   80.2        52        4170     <- known good, the control
 *        25   2   40.1       104        4170
 *        51   4   20.05      208        4170
 *       103   8   10.03      416        4173
 *       207  16    5.01      832        4170
 * Then the same three big frames at DOUBLE the rate, to see whether a lower packet rate
 * buys headroom above 4170 (it would, if the cap is packets per connection event):
 *        51   2   40.1       208        8341
 *       103   4   20.05      416        8341
 *       207   8   10.03      832        8345
 *
 * Reading it:
 *   all five iso-rate rungs clean            -> BYTE-limited; trade cadence for width freely
 *   big ones fail while 12/1 stays clean     -> per-frame size limit (BLE fragmentation)
 *   the doubled-rate rungs also come through -> PACKET-limited; big+slow beats small+fast
 *
 * The DMA-busy guard is not binding: at 913043 baud even the 832 B frame clocks out in
 * 9.11 ms against the 12.47 ms tick. It would only bite past ~1100 B, where transfers
 * straddle the tick and halve the rate -- which would look like radio loss and be a
 * measurement artifact.
 *
 * float[1]=floats and float[2]=divider label each rung, because two rungs share the same
 * frame size and only the cadence differs. Loss must be read from consecutive float[0]
 * deltas WITHIN one contiguous rung, never max-min and never across rungs.
 *
 * FOURTH SWEEP (this one) -- where is the new byte ceiling?
 * `AT+AINTVL=20` sent to the dongle (seller's command, button held, USB power cycle)
 * KILLED the per-frame size limit: the iso-rate ladder above went from 0.81/9.09/25.86/
 * 22.96/34.29 % loss to 0.00 % on all five rungs, 832 B frames included. Frame size no
 * longer matters, so cadence can be traded for width -- 207 floats at 5 Hz is lossless.
 *
 * What did NOT move is the byte ceiling: 8341 B/s still collapses (37.84/62.31/89.47 %).
 * So the cap is now a clean BYTE RATE somewhere in 4170..8341, and the only two rates
 * ever offered were those endpoints. This ladder fills the gap by holding cadence at a
 * constant 20.05 Hz (divider 4) and stepping frame size, so offered rate is the ONLY
 * variable:
 *     floats  frame B   offered B/s        floats  frame B   offered B/s
 *        51     208        4170               77     312        6256
 *        58     236        4732               83     336        6737
 *        64     260        5213               90     364        7300
 *        70     284        5694              103     416        8341
 *
 * All dividers are 4 here, so float[2] is constant and float[1] alone labels the rung --
 * every frame size in the ladder is distinct. At 913043 baud the largest frame (416 B)
 * clocks out in 4.56 ms against a 12.47 ms tick, so the DMA guard cannot bind.
 *
 * Result: clean to 6256 B/s (312 B @ 20.05 Hz, 0.00%), 6.12% at 6737, collapse above.
 *
 * FIFTH SWEEP (this one) -- push to the wire limit and see where it actually dies.
 * Target asked for: ~90 kB/s. Two hard walls sit below that, so this ladder is really a
 * measurement of how far short the radio falls and where:
 *   1. THE UART. At 913043 baud 8N1 = 10 bits/byte, the wire carries 91304 B/s at 100%
 *      utilisation. 90 kB/s (92160 B/s) is ABOVE that -- unreachable at this baud no
 *      matter how good the radio is. The top rung below sits at 98.7% of the wire.
 *   2. THE TICK. Send_Task runs at 80.2 Hz (12.47 ms). A 1124 B frame clocks out in
 *      12.31 ms -- 160 us of margin. If a transfer straddles the tick the DMA guard
 *      skips the next emission and the cadence HALVES. That is a measurement artifact,
 *      not radio loss, so the host must measure actual Hz from arrival times rather
 *      than assume 80.2/divider.
 * All dividers are 1 (every tick), so only frame size varies:
 *     floats  frame B   offered B/s        floats  frame B   offered B/s
 *        18      76        6095              120     484       38817
 *        30     124        9945              170     684       54857
 *        50     204       16361              220     884       70897
 *        80     324       25985              280    1124       90145
 */
/* PRODUCTION IMAGE 2026-08-09 — flip to 1 only to re-measure the ladder in
 * flight. The 16-byte attitude triple below is what the link carries when no
 * 0x21 stream is subscribed. See session 2026-08-09-micoair-summary.md for the
 * ladder history and the 90363 B/s at 0.00% loss measurement. */
#define USART3_THROUGHPUT_TEST   0
#define USART3_TEST_FLOAT_LADDER { 18, 30, 50, 80, 120, 170, 220, 280 }
#define USART3_TEST_DIV_LADDER   {  1,  1,  1,  1,   1,   1,   1,   1 }
#define USART3_TEST_MAX_FLOATS   280
#define USART3_TEST_STEP_TICKS   560    /* ~7 s per rung at 80.2 Hz, regardless of divider */

#if USART3_THROUGHPUT_TEST
#define USART3_TX_LEN            (USART3_TEST_MAX_FLOATS * 4 + 4)   /* 1124 */
#else
#define USART3_TX_LEN            16
#endif

void usart3_send(void)
{
	/* Staging buffer only: Usart3_Stream_TxSend() copies out of it before
	 * returning, so the DMA never reads it. Still static rather than a local
	 * because 1124 B would not fit on Send_Task's stack. */
	static UCHAR8 str_USART[USART3_TX_LEN];

	/* The throughput ladder varies the frame length at runtime, so the DMA transfer
	 * count can no longer be the compile-time USART3_TX_LEN -- that is now only the
	 * buffer's maximum size, not the length of any particular frame. */
	USHORT16 tx_len = USART3_TX_LEN;

	/* A 0x09 subscribe stream routed to USART3 stands this frame down. The TX
	 * ring made that arbitration unnecessary for SAFETY -- both producers queue
	 * whole frames and can no longer retarget M0AR under each other -- but it is
	 * still the right POLICY: interleaving two unrelated frame formats on one
	 * link just makes the host guess. The subscription wins, being the variable
	 * set the operator explicitly asked for, whereas this frame is a fixed
	 * attitude triple (or the throughput-test pattern). */
	if (Subscribe_StreamOwnsUsart3() != 0U) return;

	/* NO skip-if-busy guard any more. USART3 is now a continuous ring
	 * (BSP/usart3.c): Usart3_Stream_TxSend() copies the frame and returns, so
	 * str_USART is free immediately and emission is decoupled from this tick.
	 * The old guard returned early whenever DMA1_Stream3 was still draining,
	 * which made FRAME SIZE the limit rather than byte rate -- measured
	 * 2026-08-09 an 884 B frame straddled the ~12.47 ms tick, every other tick
	 * was refused and the cadence halved to 48.3 Hz, while the radio itself lost
	 * nothing (0.00 % at every rung). */

#if USART3_THROUGHPUT_TEST
	{
		static const USHORT16 f_ladder[] = USART3_TEST_FLOAT_LADDER;
		static const UCHAR8   d_ladder[] = USART3_TEST_DIV_LADDER;
		static FP32     seq   = 0.0f; /* exact in float to 2^24 frames = 46 h at 100 Hz */
		static UCHAR8   rung  = 0;    /* index into the two ladders */
		static USHORT16 ticks = 0;    /* Send_Task ticks spent at this rung */
		static UCHAR8   phase = 0;    /* tick counter within the divider */
		USHORT16 nfloats;
		UCHAR8   divider;
		FP32 v;
		int  i;

		/* Rung advances on TICKS, not on frames emitted: a divider of 16 emits 16x
		 * fewer frames, so a frame-based hold would leave the slow rungs running 16x
		 * longer and skew the capture. */
		if(++ticks >= USART3_TEST_STEP_TICKS)
		{
			ticks = 0;
			phase = 0;
			rung++;
			if(rung >= (UCHAR8)(sizeof(d_ladder) / sizeof(d_ladder[0]))) rung = 0;
		}

		nfloats = f_ladder[rung];
		divider = d_ladder[rung];

		/* Emit on every Nth tick. Returning here leaves the previous frame's buffer
		 * untouched, which is safe: the DMA finished it long ago (worst case 9.11 ms
		 * against a 12.47 ms tick) and we are not re-arming the stream. */
		if(++phase < divider) return;
		phase = 0;

		tx_len = (USHORT16)(nfloats * 4U + 4U);

		seq += 1.0f;
		for(i = 0; i < (int)nfloats; i++)
		{
			if     (i == 0) v = seq;              /* host counts gaps in this */
			else if(i == 1) v = (FP32)nfloats;    /* labels the rung: size ... */
			else if(i == 2) v = (FP32)divider;    /* ... and cadence, since sizes repeat */
			else if(i == 3) v = (FP32)UA3TxDrops; /* FC-side ring drops, NOT air loss */
			else            v = (FP32)i;          /* fixed pattern: any corruption shows */

			str_USART[i * 4 + 0] = BYTE0(v);
			str_USART[i * 4 + 1] = BYTE1(v);
			str_USART[i * 4 + 2] = BYTE2(v);
			str_USART[i * 4 + 3] = BYTE3(v);
		}
		str_USART[tx_len - 4] = 0x00;
		str_USART[tx_len - 3] = 0x00;
		str_USART[tx_len - 2] = 0x80;
		str_USART[tx_len - 1] = 0x7f;
	}
#else
	float angles[3] ;
	angles[0] = imu_data.rol;
	angles[1] = imu_data.pit;
	angles[2] = imu_data.yaw;
	//angles[0] = 0;
	//angles[1] = 0;
  //angles[2] = 0;
	str_USART[0] = BYTE0(angles[0]);
	str_USART[1] = BYTE1(angles[0]);
	str_USART[2] = BYTE2(angles[0]);
	str_USART[3] = BYTE3(angles[0]);

	str_USART[4] = BYTE0(angles[1]);
	str_USART[5] = BYTE1(angles[1]);
	str_USART[6] = BYTE2(angles[1]);
	str_USART[7] = BYTE3(angles[1]);	
	
	str_USART[8] = BYTE0(angles[2]);
	str_USART[9] = BYTE1(angles[2]);
	str_USART[10] = BYTE2(angles[2]);
	str_USART[11] = BYTE3(angles[2]);	
	
	str_USART[12] = 0x00;
	str_USART[13] = 0x00;
	str_USART[14] = 0x80;
	str_USART[15] = 0x7f;
#endif
	
	
  /* Queue it. Returns 0 only when the ring is full, i.e. the wire cannot keep
   * up -- that is FC-side loss, counted in UA3TxDrops and stamped into float[3]
   * above so the host can tell it apart from air loss. */
  (void)Usart3_Stream_TxSend(str_USART, tx_len);
}

/* Max frame: 6-byte header + payload + 1 CRC; Frame B payload up to ~326 bytes @ MAX_NUM_BASIS=8 */
UCHAR8 Buf_Telemetry_UART4[512] = {0};

/* OF-calibration frame 0x05 sources: body-frame accel (bmi088_driver.c, mg) and the v3 OF
 * velocity bias (StabilizerTask.c). FP32 is float; the extern re-declares are type-compatible. */
extern FP32 Acc_X_Real;
extern FP32 Acc_Y_Real;
extern float s_of_bias_x, s_of_bias_y;
extern float Lin_Acc_X_body, Lin_Acc_Y_body, Lin_Acc_Z_body;   /* gravity-removed body accel (mg), imu_update.c */

/* mg -> m/s^2. NOT 0.001: that yields g, not m/s^2 (1000 mg = 1 g = 9.81 m/s^2).
 * The EKF's R_of (6.16e-4 m^2/s^2) and Q are specified in SI, so feeding the predict
 * step in g made the inertial contribution ~9.81x too small against the m/s optical-flow
 * update. sim/tools/replay_ekf_flight.py:40 carries the same mislabelled 0.001. */
#define ACC_MG_TO_MS2  0.00981f

/* Optical-flow scale: metres/second per raw of2_dx_fix count, for the EKF measurement.
 * UNRESOLVED INCONSISTENCY — left at the historical 0.01 deliberately, do not change
 * without deciding: docs/tracking_baseline_and_drift.md:224 measured X = 0.0124 +/- 0.0009
 * m per raw*s from ~92 cm hand-slides, and ADR-0011:131 derives this EKF's own
 * R_of = 6.16e-4 = 0.0124^2 * 4 raw^2 from that same number. So R_of assumes 0.0124 while
 * the measurement it scales uses 0.01 — the filter is fed a velocity ~19 % small relative
 * to the noise model tuned for it. sim/tools/replay_ekf_flight.py:39 has the same 0.01, so
 * the golden replay inherits the mismatch rather than contradicting it. */
#define OF_LSB_MPS  0.01f

void Send_Groundstation_Telemetry_UART4(void)
{
    static uint8_t frame_counter = 0;
    uint16_t len = 0;
    uint8_t crc = 0;
    int i;
    uint8_t dma_busy = 0U;
    /* Set to 1 when the built frame(s) already carry their own trailing checksum
     * (Frame A closes its own CRC8 before Frame C is appended). When set, the
     * shared XOR-CRC8 at the end of this function is skipped so it does not
     * clobber the framing. Stays 0 for single-frame buffers (B / ID / bench / OF). */
    uint8_t frame_self_crc = 0;
    uint32_t t_prof_sec;

    PlatformObservability_Tick((uint16_t)((gs_cmd_head + 16U - gs_cmd_tail) % 16U),
                               (uint16_t)(Usart3_Stream_Busy() != 0U));

    /* Wait for the PREVIOUS UART5 transfer to drain BEFORE touching the buffer.
     *
     * DMA1_Stream7 reads Buf_Telemetry_UART4 asynchronously, so every write below
     * lands in memory the hardware may still be transmitting. The drain-wait used to
     * sit next to the DMA re-arm at the bottom of this function, ~580 lines after the
     * first write here -- which is far too late to protect the buffer.
     *
     * Frame A (48 B) and the A+C burst (102 B) drain in 4-9 ms, inside the ~18 ms task
     * period, so they were never hurt. Frame B is 305 B = 26.5 ms at 115200: still in
     * flight when the next tick arrives. That tick rebuilt the buffer underneath it, so
     * the DMA transmitted Frame B's head followed by the NEW frame's bytes, then sent
     * that new frame again in full. Measured on the wire 2026-07-29: 33 of 34 Frame B
     * were 407 bytes instead of 305 -- exactly 305 + 102 -- and 33/34 failed CRC, which
     * is why the PID/position panels showed zeros and occasional absurd floats.
     *
     * Waiting here costs nothing when the previous frame has already drained (the common
     * case) and simply paces this task to the link when it has not. */
    /* Non-blocking check: if previous UART5 DMA transfer is still active, do not busy-wait */
    dma_busy = ((DMA_GetCurrDataCounter(DMA1_Stream7) != 0U) || (DMA_GetCmdStatus(DMA1_Stream7) == ENABLE)) ? 1U : 0U;

    /* EKF step: runs every Send_Task tick, unconditionally — independent of which
     * telemetry frame this tick happens to emit. NOTE the tick is 100 Hz in normal
     * flight, not 200 Hz (main.c pacing); see the dt comment below. Previously this lived inside
     * the `of_frame_on` branch below, which meant the estimator only ran while the
     * ground station had frame 0x05 selected (nothing toggles that in normal
     * operation), silently defeating the EKF_RUN_ENABLED/EKF_TELEM_ENABLED split
     * meant to decouple "does it run" from "do we transmit it" (ADR-0011).
     * IMU accel is in mg -> convert to m/s^2. */
    t_prof_sec = DWT->CYCCNT;
    if (!s_ekf_inited) {
        Ekf9_Init(&s_ekf, EKF_RUN_ENABLED);
        s_ekf_inited = 1U;
    }
    if (s_ekf.active) {
        /* Predict input must be GRAVITY-REMOVED linear acceleration, not raw specific
         * force. Feeding Acc_*_Real (which reads +1 g on Z at rest) left b_a as the only
         * free state able to cancel it, so b_a converged to the gravity projection:
         * measured on the bench, b_a matched Acc_*_Real to a ratio of 0.999/1.004/1.000
         * on x/y/z (b_a.z = +1008 mg). With b_a == a, `v += (a - b_a)*dt` contributes
         * exactly nothing and the filter degenerates into a low-pass on the OF velocity
         * (NIS ~1e-5, no innovation). sim/ekf.py's predict docstring says "gravity NOT
         * removed (caller removes)" — this is the caller. The golden replay hid the bug
         * by hard-coding a_body[2] = 0.0, so gravity was never presented to it. */
        float ax = Lin_Acc_X_body * ACC_MG_TO_MS2;
        float ay = Lin_Acc_Y_body * ACC_MG_TO_MS2;
        float az = Lin_Acc_Z_body * ACC_MG_TO_MS2;
        /* dt is MEASURED, not assumed. This function is called from Send_Task, which
         * paces at 10 ms (100 Hz) in normal flight and only ~5 ms (200 Hz) while
         * id_frame_on/of_frame_on is set (main.c) — and the 73 B EKF-telemetry variant
         * of frame 0x05 needs ~6.7 ms on the 115200 link, so it overruns even that 5 ms
         * budget via the DMA busy-wait at the bottom of this function. A hardcoded
         * 0.005f was therefore wrong by 2x in normal flight and by ~1.3x while logging,
         * and it CHANGED when logging was switched on — i.e. observing the estimator
         * altered it. Tick resolution is 1 ms (configTICK_RATE_HZ=1000); per-sample
         * quantisation is harmless because tick deltas sum to exact elapsed time. */
        static TickType_t s_ekf_last_tick = 0;
        TickType_t now_tick = xTaskGetTickCount();
        float dt = (s_ekf_last_tick == 0)
                 ? (1.0f / configTICK_RATE_HZ) * 10.0f   /* first call: assume 100 Hz */
                 : (float)(now_tick - s_ekf_last_tick) * (1.0f / configTICK_RATE_HZ);
        s_ekf_last_tick = now_tick;
        if (dt < 0.001f) { dt = 0.001f; }   /* guard against tick wrap / scheduler hiccup */
        if (dt > 0.050f) { dt = 0.050f; }
        Ekf9_Predict(&s_ekf, ax, ay, az, Gyro_X_Real, Gyro_Y_Real, Gyro_Z_Real, dt);
        /* OF measurement: quality gate (same threshold as control loop).
         * Must be DEBIASED. of2_dx_fix carries a power-cycle-dependent zero-point
         * offset (observed 6..7 raw counts one session, 1 the next); the 9-state vector
         * is [v(3), b_a(3), b_g(3)] with NO optical-flow bias state, so feeding the raw
         * value gives the filter an unmodellable constant velocity error that it can
         * only absorb into b_a — which is why b_a read ~10.8 mg on the bench against a
         * <2 mg golden-replay expectation. Subtracting s_of_bias_x/y is also what the
         * control path does (StabilizerTask.c), so both estimators now see one signal. */
        if (ano_of.of_quality >= 50) {
            float ofx = ((float)ano_of.of2_dx_fix - s_of_bias_x) * OF_LSB_MPS;
            float ofy = ((float)ano_of.of2_dy_fix - s_of_bias_y) * OF_LSB_MPS;
            Ekf9_UpdateOf(&s_ekf, ofx, ofy);
        }
        /* NO accelerometer measurement update — deliberate, do not re-add.
         * Ekf9_UpdateAccXY sets H to select v_body[0..1] and then passes it a *linear
         * acceleration* as z, so its innovation is (acceleration - velocity): a unit
         * mismatch, telling the filter "your velocity equals 1.9 mg". Worse, the
         * accelerometer is already the input to Ekf9_Predict above; using it a second
         * time as a measurement double-counts one sensor, and that is what let b_a slide
         * until it exactly cancelled a. The genuine velocity measurements are optical
         * flow (XY, above) and Z-rate (below); b_a stays observable through the OF update
         * via the P[0,3] cross-covariance restored by the 2026-07-24 F-cross-term fix.
         * NOTE b_a therefore needs motion to converge — on a static bench it will simply
         * decay toward zero rather than identify a bias. */
        /* Z-rate: use the smoothed derivative of of2_h computed in StabilizerTask
         * (of2_h_f2_v is the LP-filtered d(altitude)/dt, m/s). This is the same
         * signal that feeds Ctrler.Z_ratePID.FB so the EKF and the control loop
         * see the same vertical velocity. */
        Ekf9_UpdateZRate(&s_ekf, ano_of.of2_h_f2_v);
    }
    SendProf_Record(&g_send_prof.sec_ekf, t_prof_sec);

    /* UART5 still draining the previous burst: skip this tick's A/B frame
     * instead of spinning. A staged subscribe request stays pending
     * (UA5RxSubscribePending) and is answered on the next free tick, since
     * its reply also needs DMA1_Stream7. */
    if (dma_busy != 0U) {
        t_prof_sec = DWT->CYCCNT;
        Subscribe_StreamTick();
        SendProf_Record(&g_send_prof.sec_subscribe_tick, t_prof_sec);
        return;
    }

    t_prof_sec = DWT->CYCCNT;
    Buf_Telemetry_UART4[0] = 0xAA;
    Buf_Telemetry_UART4[1] = 0xBB;

    if (motor_test_active) // FRAME 0x04 — motor bench-test stream @100Hz (replaces A/B while active)
    {
        // Payload (20 B): u32 sample_counter, u8 motor_id, u16 commanded_ccr, f real_voltage, u8 active,
        // {u16 rpm0, u16 rpm1, u16 rpm2, u16 rpm3} — ADR-0010. Layout `<I B H f B 4H`.
        // Thrust is read off an external scale by hand; the firmware streams the operating point
        // (which motor, what CCR, pack voltage, measured RPM) at the loop-adjacent rate so the dashboard
        // logs each manually-entered thrust point against fresh voltage and measured ω. Get_Voltage() is
        // called here because SystemMonitor_Task only refreshes real_voltage at 1 Hz (too slow to catch
        // pack sag during a sweep). sample_counter detects dropped frames. RPMs are averaged per-channel
        // and stale channels read 0 (sensor unplugged / motor stopped). See docs/bench_characterization.md.
        static uint32_t bench_sample_counter = 0;
        uint16_t payload_len = 20U;
        float tf;
        uint16_t rpm_vals[RPM_NUM_CH];
        uint8_t i;

        for (i = 0; i < RPM_NUM_CH; i++) {
            rpm_vals[i] = RPM_Get(i);
        }

        Get_Voltage(); // fresh pack voltage per frame (overrides the 1 Hz SystemMonitor read)

        Buf_Telemetry_UART4[2] = 0x04; // bench frame type
        Buf_Telemetry_UART4[3] = (uint8_t)(payload_len >> 8);
        Buf_Telemetry_UART4[4] = (uint8_t)(payload_len & 0xFFU);
        Buf_Telemetry_UART4[5] = MAX_NUM_BASIS; // header parity with other frames
        len = 6;

        Buf_Telemetry_UART4[len++] = (uint8_t)(bench_sample_counter & 0xFFU);
        Buf_Telemetry_UART4[len++] = (uint8_t)((bench_sample_counter >> 8) & 0xFFU);
        Buf_Telemetry_UART4[len++] = (uint8_t)((bench_sample_counter >> 16) & 0xFFU);
        Buf_Telemetry_UART4[len++] = (uint8_t)((bench_sample_counter >> 24) & 0xFFU);

        Buf_Telemetry_UART4[len++] = motor_test_id;
        Buf_Telemetry_UART4[len++] = (uint8_t)(motor_test_ccr & 0xFFU);
        Buf_Telemetry_UART4[len++] = (uint8_t)((motor_test_ccr >> 8) & 0xFFU);

        tf = real_voltage;
        Buf_Telemetry_UART4[len++] = BYTE0(tf);
        Buf_Telemetry_UART4[len++] = BYTE1(tf);
        Buf_Telemetry_UART4[len++] = BYTE2(tf);
        Buf_Telemetry_UART4[len++] = BYTE3(tf);

        Buf_Telemetry_UART4[len++] = motor_test_active;

        // ADR-0010: 4 channels × u16 little-endian RPM after the active byte.
        // Reordered by channel index, NOT motor number — physical plugging decides pairing.
        for (i = 0; i < RPM_NUM_CH; i++) {
            Buf_Telemetry_UART4[len++] = (uint8_t)(rpm_vals[i] & 0xFFU);
            Buf_Telemetry_UART4[len++] = (uint8_t)((rpm_vals[i] >> 8) & 0xFFU);
        }
        bench_sample_counter++;
    }
    else if (mrac_flags.id_frame_on) // FRAME 0x03 — high-rate system-ID stream @100Hz (replaces A/B while active)
    {
        // Payload (36 B): u32 sample_counter, u8 axis_id, {r,x,u_nom,u_ad,xm} floats for THE EXCITED
        // AXIS ONLY, f sysid_dither, f real_voltage, u8 ARM, u8 FlyMode, u8 SysID FSM state.
        // Single-axis: a run excites one axis; logging only it shrinks the frame from 95->36 B so the
        // GS frame's DMA busy-wait drops ~8.5->~3.7 ms, letting Send_Task stream at 200 Hz (1:1 with
        // the control loop) for clean 2nd-order+ plant ID. axis_id (0 pitch,1 roll,2 yaw,3 z) tells
        // the GS which axis these signals belong to.
        // sysid_dither = raw excitation (exogenous instrument) for unbiased closed-loop IV ID.
        // u_nom and u_ad kept SEPARATE: u_nom+u_ad is the plant input; u_ad alone is the adaptive signal.
        // sample_counter is the firmware time base (detects dropped frames via counter gaps).
        static uint32_t id_sample_counter = 0;
        MRAC_AxisState_t* idax[4];
        MRAC_AxisState_t* a;
        uint8_t axis_id = SysID_GetAxis();
        float idvals[5];
        uint16_t payload_len = 36U;
        int k;

        Buf_Telemetry_UART4[2] = 0x03; // ID frame type
        Buf_Telemetry_UART4[3] = (uint8_t)(payload_len >> 8);
        Buf_Telemetry_UART4[4] = (uint8_t)(payload_len & 0xFFU);
        Buf_Telemetry_UART4[5] = MAX_NUM_BASIS;
        len = 6;

        Buf_Telemetry_UART4[len++] = (uint8_t)(id_sample_counter & 0xFFU);
        Buf_Telemetry_UART4[len++] = (uint8_t)((id_sample_counter >> 8) & 0xFFU);
        Buf_Telemetry_UART4[len++] = (uint8_t)((id_sample_counter >> 16) & 0xFFU);
        Buf_Telemetry_UART4[len++] = (uint8_t)((id_sample_counter >> 24) & 0xFFU);

        Buf_Telemetry_UART4[len++] = axis_id;

        idax[0] = &mrac_state.pitch; idax[1] = &mrac_state.roll;
        idax[2] = &mrac_state.yaw;   idax[3] = &mrac_state.z_rate;
        a = idax[axis_id & 0x03U];
        idvals[0] = a->r;      // rate command into axis (rad/s)
        idvals[1] = a->x;      // measured rate (rad/s)
        idvals[2] = a->u_nom;  // nominal PID effort (SI Nm / N)
        idvals[3] = a->u_ad;   // adaptive effort (SI Nm / N) — plant input = u_nom + u_ad
        idvals[4] = a->xm;     // reference-model state (rad/s)
        for (k = 0; k < 5; k++) {
            Buf_Telemetry_UART4[len++] = BYTE0(idvals[k]);
            Buf_Telemetry_UART4[len++] = BYTE1(idvals[k]);
            Buf_Telemetry_UART4[len++] = BYTE2(idvals[k]);
            Buf_Telemetry_UART4[len++] = BYTE3(idvals[k]);
        }
        {
            float dval = SysID_GetDither(); // exogenous excitation -> clean IV instrument offline
            Buf_Telemetry_UART4[len++] = BYTE0(dval);
            Buf_Telemetry_UART4[len++] = BYTE1(dval);
            Buf_Telemetry_UART4[len++] = BYTE2(dval);
            Buf_Telemetry_UART4[len++] = BYTE3(dval);
        }
        {
            // real_voltage is refreshed at 1 Hz by Get_Voltage() in SystemMonitor_Task (battery
            // changes slowly; that single periodic read also drives the low-battery beep and the
            // dashboard battery widget in all modes). Operating-point voltage matters for SysID
            // because actuator effectiveness scales with pack voltage -> it modulates the gain K.
            float tf = real_voltage;
            Buf_Telemetry_UART4[len++] = BYTE0(tf);
            Buf_Telemetry_UART4[len++] = BYTE1(tf);
            Buf_Telemetry_UART4[len++] = BYTE2(tf);
            Buf_Telemetry_UART4[len++] = BYTE3(tf);
        }
        Buf_Telemetry_UART4[len++] = DroneStatus.ARM_Status;
        Buf_Telemetry_UART4[len++] = DroneStatus.FlyMode;
        Buf_Telemetry_UART4[len++] = SysID_GetState(); // live FSM state for the GS System ID tab (ADR-0004 dec.6)
        id_sample_counter++;
    }
    else if (mrac_flags.of_frame_on) // FRAME 0x05 — OF calibration/fusion raw stream @200Hz (replaces A/B while active)
    {
        // Payload layout (v14):
        //  2   u16 sample_counter
        //  4   s16 of2_dx_fix/dy_fix    (0.01 m/s, tilt-comp body velocity)
        //  8   s16 of2_dx/dy            (0.01 m/s, raw velocity cross-check)
        //  4   s16 Acc_X/Y_Real          (1 mg, body-frame accel incl gravity)
        //  4   s16 Lin_Acc_X/Y_body      (1 mg, gravity-removed fusion input)
        //  6   s16 yaw/pit/rol           (0.01 deg)
        //  4   s16 s_of_bias_x/y         (0.01 raw units, firmware v3 bias)
        //  2   u16 of_alt_cm             (cm)
        //  8   f earth_x/earth_y         (integrated world position, m)
        //  1   u8  of_quality
        // --- ADR-0011 always-on additions ---
        //  6   s16 acc_bias[3]           (1 mg, s_cal_trim.b_a)
        //  6   s16 gyro_bias[3]          (1e-4 rad/s, s_cal_hot.b_g)
        //  2   u16 cal_health            (bitmask from g_cal_health)
        //  1   u8  bias_mode             (0=FIXED 1=EMA 2=EKF, CMD 0x1E)
        //  1   u8  bias_ema_freeze       (0=run 1=frozen, CMD 0x1E idx=1)
        // --- ADR-0011 EKF additions (EKF_TELEM_ENABLED=1 only) ---
        //  6   s16 v_body[3]            (1 mm/s, EKF x[0..2])
        //  6   s16 P_diag[3]            (1e-3, EKF P[0,0],P[1,1],P[2,2])
        //  2   s16 NIS                  (1e-3)
        //  6   s16 K_last[3]            (1e-3, K[0..2])
        // Total: 39 + 16 = 55 B always-on; 55 + 20 = 75 B with EKF_TELEM_ENABLED=1
        static uint16_t of_sample_counter = 0;
        uint16_t payload_len = (EKF_TELEM_ENABLED) ? 75U : 55U;
        int16_t s16v;
        float ex, ey;

        Buf_Telemetry_UART4[2] = 0x05; // OF calibration frame type
        Buf_Telemetry_UART4[3] = (uint8_t)(payload_len >> 8);
        Buf_Telemetry_UART4[4] = (uint8_t)(payload_len & 0xFFU);
        Buf_Telemetry_UART4[5] = MAX_NUM_BASIS;
        len = 6;

        Buf_Telemetry_UART4[len++] = (uint8_t)(of_sample_counter & 0xFFU);
        Buf_Telemetry_UART4[len++] = (uint8_t)((of_sample_counter >> 8) & 0xFFU);

        #define OF_PUT_S16(_v) do { s16v = (int16_t)(_v); \
            Buf_Telemetry_UART4[len++] = (uint8_t)((uint16_t)s16v & 0xFFU); \
            Buf_Telemetry_UART4[len++] = (uint8_t)(((uint16_t)s16v >> 8) & 0xFFU); } while(0)

        OF_PUT_S16(ano_of.of2_dx_fix);
        OF_PUT_S16(ano_of.of2_dy_fix);
        OF_PUT_S16(ano_of.of2_dx);
        OF_PUT_S16(ano_of.of2_dy);
        OF_PUT_S16(Acc_X_Real);            // body-frame accel, mg (gravity-included)
        OF_PUT_S16(Acc_Y_Real);
        OF_PUT_S16(Lin_Acc_X_body);        // gravity-removed body accel, mg (fusion input)
        OF_PUT_S16(Lin_Acc_Y_body);
        OF_PUT_S16(imu_data.yaw * 100.0f); // 0.01 deg
        OF_PUT_S16(imu_data.pit * 100.0f);
        OF_PUT_S16(imu_data.rol * 100.0f);
        OF_PUT_S16(s_of_bias_x * 100.0f);  // 0.01 raw units
        OF_PUT_S16(s_of_bias_y * 100.0f);
        #undef OF_PUT_S16

        Buf_Telemetry_UART4[len++] = (uint8_t)(ano_of.of_alt_cm & 0xFFU);
        Buf_Telemetry_UART4[len++] = (uint8_t)((ano_of.of_alt_cm >> 8) & 0xFFU);

        ex = ano_of.earth_x;
        Buf_Telemetry_UART4[len++] = BYTE0(ex);
        Buf_Telemetry_UART4[len++] = BYTE1(ex);
        Buf_Telemetry_UART4[len++] = BYTE2(ex);
        Buf_Telemetry_UART4[len++] = BYTE3(ex);
        ey = ano_of.earth_y;
        Buf_Telemetry_UART4[len++] = BYTE0(ey);
        Buf_Telemetry_UART4[len++] = BYTE1(ey);
        Buf_Telemetry_UART4[len++] = BYTE2(ey);
        Buf_Telemetry_UART4[len++] = BYTE3(ey);

        Buf_Telemetry_UART4[len++] = ano_of.of_quality;

        /* ADR-0011 §"Telemetry surface": always-on calibration fields */
        /* acc_bias[3]: s16, 1 mg scale (s_cal_trim.b_a is already in mg).
         * Hand-rolled byte stores to match the existing tail pattern (OF_PUT_S16 was
         * #undef'd at line 552 when the alt/earth/quality fields moved to raw bytes). */
        {
            int16_t _v;
            _v = (int16_t)s_cal_trim.b_a[0]; Buf_Telemetry_UART4[len++] = BYTE0(_v); Buf_Telemetry_UART4[len++] = BYTE1(_v);
            _v = (int16_t)s_cal_trim.b_a[1]; Buf_Telemetry_UART4[len++] = BYTE0(_v); Buf_Telemetry_UART4[len++] = BYTE1(_v);
            _v = (int16_t)s_cal_trim.b_a[2]; Buf_Telemetry_UART4[len++] = BYTE0(_v); Buf_Telemetry_UART4[len++] = BYTE1(_v);
            /* gyro_bias[3]: s16, 1e-4 rad/s scale */
            _v = (int16_t)(s_cal_hot.b_g[0] * 1e4f); Buf_Telemetry_UART4[len++] = BYTE0(_v); Buf_Telemetry_UART4[len++] = BYTE1(_v);
            _v = (int16_t)(s_cal_hot.b_g[1] * 1e4f); Buf_Telemetry_UART4[len++] = BYTE0(_v); Buf_Telemetry_UART4[len++] = BYTE1(_v);
            _v = (int16_t)(s_cal_hot.b_g[2] * 1e4f); Buf_Telemetry_UART4[len++] = BYTE0(_v); Buf_Telemetry_UART4[len++] = BYTE1(_v);
        }
        /* cal_health: u16 bitmask */
        Buf_Telemetry_UART4[len++] = (uint8_t)(g_cal_health & 0xFFU);
        Buf_Telemetry_UART4[len++] = (uint8_t)((g_cal_health >> 8) & 0xFFU);

        /* bias_mode / bias_ema_freeze: always-on, no #if guard */
        Buf_Telemetry_UART4[len++] = g_of_bias_mode;
        Buf_Telemetry_UART4[len++] = g_of_bias_ema_freeze;

        /* EKF step now runs unconditionally above (before the frame-select chain);
         * this branch only emits its telemetry when EKF_TELEM_ENABLED. */
#if EKF_TELEM_ENABLED
        /* EKF telemetry: v_body[3] (mm/s), P_diag[3] (1e-3), NIS (1e-3), K_last[3] (1e-3).
         * Hand-rolled byte stores (same pattern as the acc/gyro bias fields above). */
        {
            int16_t _v;
            _v = (int16_t)(s_ekf.x[0] * 1000.0f); Buf_Telemetry_UART4[len++] = BYTE0(_v); Buf_Telemetry_UART4[len++] = BYTE1(_v);
            _v = (int16_t)(s_ekf.x[1] * 1000.0f); Buf_Telemetry_UART4[len++] = BYTE0(_v); Buf_Telemetry_UART4[len++] = BYTE1(_v);
            _v = (int16_t)(s_ekf.x[2] * 1000.0f); Buf_Telemetry_UART4[len++] = BYTE0(_v); Buf_Telemetry_UART4[len++] = BYTE1(_v);
            _v = (int16_t)(s_ekf.P[0 * 9U + 0U] * 1e3f); Buf_Telemetry_UART4[len++] = BYTE0(_v); Buf_Telemetry_UART4[len++] = BYTE1(_v);
            _v = (int16_t)(s_ekf.P[1 * 9U + 1U] * 1e3f); Buf_Telemetry_UART4[len++] = BYTE0(_v); Buf_Telemetry_UART4[len++] = BYTE1(_v);
            _v = (int16_t)(s_ekf.P[2 * 9U + 2U] * 1e3f); Buf_Telemetry_UART4[len++] = BYTE0(_v); Buf_Telemetry_UART4[len++] = BYTE1(_v);
            _v = (int16_t)(s_ekf.nis * 1e3f);         Buf_Telemetry_UART4[len++] = BYTE0(_v); Buf_Telemetry_UART4[len++] = BYTE1(_v);
            _v = (int16_t)(s_ekf.k_last[0] * 1e3f);  Buf_Telemetry_UART4[len++] = BYTE0(_v); Buf_Telemetry_UART4[len++] = BYTE1(_v);
            _v = (int16_t)(s_ekf.k_last[1] * 1e3f);  Buf_Telemetry_UART4[len++] = BYTE0(_v); Buf_Telemetry_UART4[len++] = BYTE1(_v);
            _v = (int16_t)(s_ekf.k_last[2] * 1e3f);  Buf_Telemetry_UART4[len++] = BYTE0(_v); Buf_Telemetry_UART4[len++] = BYTE1(_v);
        }
#endif
        #undef OF_PUT_S16

        of_sample_counter++;
    }
    else if (frame_counter % 5 != 0) // 100Hz Frame A
    {
        // FRAME A �� header: [type][LEN_hi][LEN_lo][MAX_NUM_BASIS], payload 37 bytes (16-bit LEN)
        {
            uint16_t payload_len = 41U; /* +1 rc_authority +1 of_hold +1 estimator_ready +1 GS_PROTO_VERSION */
            Buf_Telemetry_UART4[2] = 0x01; // ID
            Buf_Telemetry_UART4[3] = (uint8_t)(payload_len >> 8);
            Buf_Telemetry_UART4[4] = (uint8_t)(payload_len & 0xFFU);
            Buf_Telemetry_UART4[5] = MAX_NUM_BASIS;
            len = 6;
        }

        // Pack floats (4 axes * 2 floats) = 8 floats = 32 bytes
        float mrac_A[8] = {
            mrac_state.pitch.e, mrac_state.pitch.u_ad,
            mrac_state.roll.e,  mrac_state.roll.u_ad,
            mrac_state.yaw.e,   mrac_state.yaw.u_ad,
            mrac_state.z_rate.e, mrac_state.z_rate.u_ad
        };
        
        for (i = 0; i < 8; i++) {
            Buf_Telemetry_UART4[len++] = BYTE0(mrac_A[i]);
            Buf_Telemetry_UART4[len++] = BYTE1(mrac_A[i]);
            Buf_Telemetry_UART4[len++] = BYTE2(mrac_A[i]);
            Buf_Telemetry_UART4[len++] = BYTE3(mrac_A[i]);
        }
        
        Buf_Telemetry_UART4[len++] = DroneStatus.ARM_Status;
        Buf_Telemetry_UART4[len++] = DroneStatus.FlyMode;
        Buf_Telemetry_UART4[len++] = sbus_lost;
        Buf_Telemetry_UART4[len++] = (uint8_t)(TWC.execute != 0 ? 1 : 0);
        Buf_Telemetry_UART4[len++] = TWC_arrived;
        Buf_Telemetry_UART4[len++] = RCInput_GetAuthority(); /* 1=PC authority, 0=RC */
        Buf_Telemetry_UART4[len++] = g_of_hold_active; /* 1=OF position-hold, 0=angle mode (ch6) */
        Buf_Telemetry_UART4[len++] = g_estimator_ready; /* 1=estimator converged/armable, 0=warming up */
        Buf_Telemetry_UART4[len++] = GS_PROTO_VERSION; /* protocol version — must match serial_bridge.py */

        /* Close Frame A with its OWN XOR-CRC8 before appending Frame C, so Frame A
         * is a valid standalone frame on the wire. Without this, Frame C's bytes
         * follow Frame A's payload directly and the host reads Frame C's sync byte
         * as Frame A's CRC -> Frame A always fails CRC and telemetry goes stale. */
        {
            uint8_t a_crc = 0;
            uint16_t k;
            for (k = 2; k < len; k++) {
                a_crc ^= Buf_Telemetry_UART4[k];
            }
            Buf_Telemetry_UART4[len++] = a_crc;
        }
        frame_self_crc = 1; /* Frame A already has its CRC; skip the shared CRC8 below. */

        /* Frame C (0x06) — attitude / body-rate / position @ 50 Hz.
         * Built only when this is a Frame-A call (frame_counter % 10 != 5),
         * then sent back-to-back with Frame A so they are atomic on the wire.
         * A: 6+41+1 = 48 B; C: 6+46+2 = 54 B; combined = 102 B -> ~8.9 ms UART time -> fits 10 ms slot.
         * SerialBridge silently ignores 0x06 on firmware < v13 (checks via proto_version). */
        if (frame_counter % 10 != 5) {
            uint16_t c_len = 0;
            uint16_t c_payload_len;
            uint16_t c_crc;
            float tf;

            s_frame_c_buf[0] = 0xAA;
            s_frame_c_buf[1] = 0xBB;
            s_frame_c_buf[2] = 0x06;
            /* LEN (indices 3,4) is backfilled from the actual bytes written once the
             * payload is complete — see below — so it can never drift from the layout. */
            s_frame_c_buf[5] = MAX_NUM_BASIS;
            c_len = 6;

            /* rol, pit, yaw (deg) */
            tf = imu_data.rol;
            s_frame_c_buf[c_len++] = BYTE0(tf); s_frame_c_buf[c_len++] = BYTE1(tf);
            s_frame_c_buf[c_len++] = BYTE2(tf); s_frame_c_buf[c_len++] = BYTE3(tf);
            tf = imu_data.pit;
            s_frame_c_buf[c_len++] = BYTE0(tf); s_frame_c_buf[c_len++] = BYTE1(tf);
            s_frame_c_buf[c_len++] = BYTE2(tf); s_frame_c_buf[c_len++] = BYTE3(tf);
            tf = imu_data.yaw;
            s_frame_c_buf[c_len++] = BYTE0(tf); s_frame_c_buf[c_len++] = BYTE1(tf);
            s_frame_c_buf[c_len++] = BYTE2(tf); s_frame_c_buf[c_len++] = BYTE3(tf);

            /* gyro_rad[3] (rad/s) — from BMI088 driver, pre-filter */
            tf = Gyro_X_Real;
            s_frame_c_buf[c_len++] = BYTE0(tf); s_frame_c_buf[c_len++] = BYTE1(tf);
            s_frame_c_buf[c_len++] = BYTE2(tf); s_frame_c_buf[c_len++] = BYTE3(tf);
            tf = Gyro_Y_Real;
            s_frame_c_buf[c_len++] = BYTE0(tf); s_frame_c_buf[c_len++] = BYTE1(tf);
            s_frame_c_buf[c_len++] = BYTE2(tf); s_frame_c_buf[c_len++] = BYTE3(tf);
            tf = Gyro_Z_Real;
            s_frame_c_buf[c_len++] = BYTE0(tf); s_frame_c_buf[c_len++] = BYTE1(tf);
            s_frame_c_buf[c_len++] = BYTE2(tf); s_frame_c_buf[c_len++] = BYTE3(tf);

            /* earth_x, earth_y (m), altitude (m) */
            tf = ano_of.earth_x;
            s_frame_c_buf[c_len++] = BYTE0(tf); s_frame_c_buf[c_len++] = BYTE1(tf);
            s_frame_c_buf[c_len++] = BYTE2(tf); s_frame_c_buf[c_len++] = BYTE3(tf);
            tf = ano_of.earth_y;
            s_frame_c_buf[c_len++] = BYTE0(tf); s_frame_c_buf[c_len++] = BYTE1(tf);
            s_frame_c_buf[c_len++] = BYTE2(tf); s_frame_c_buf[c_len++] = BYTE3(tf);
            tf = (float)ano_of.of_alt_cm / 100.0f;
            s_frame_c_buf[c_len++] = BYTE0(tf); s_frame_c_buf[c_len++] = BYTE1(tf);
            s_frame_c_buf[c_len++] = BYTE2(tf); s_frame_c_buf[c_len++] = BYTE3(tf);

            /* rpm[4] (RPM, u16 LE) — read once, streamed in Frame C and Frame 0x04 */
            {
                uint8_t ri;
                for (ri = 0; ri < RPM_NUM_CH; ri++) {
                    uint16_t rpm = RPM_Get(ri);
                    s_frame_c_buf[c_len++] = (uint8_t)(rpm & 0xFFU);
                    s_frame_c_buf[c_len++] = (uint8_t)((rpm >> 8) & 0xFFU);
                }
            }

            /* sequence number (wrapping u16) */
            s_frame_c_buf[c_len++] = (uint8_t)(s_frame_c_seq & 0xFFU);
            s_frame_c_buf[c_len++] = (uint8_t)((s_frame_c_seq >> 8) & 0xFFU);
            s_frame_c_seq++;

            /* Backfill LEN from the actual payload written (everything after the
             * 6-byte header): currently 9 floats + 4*u16 rpm + u16 seq = 46 B. */
            c_payload_len = (uint16_t)(c_len - 6U);
            s_frame_c_buf[3] = (uint8_t)(c_payload_len >> 8);
            s_frame_c_buf[4] = (uint8_t)(c_payload_len & 0xFFU);

            /* CRC16-CCITT (XModem) over [frame_type | LEN_hi | LEN_lo | MAX_NUM_BASIS | payload]
             * = every byte from index 2 up to (but not including) the CRC itself = c_len-2. */
            c_crc = crc16_xmodem(&s_frame_c_buf[2], (uint16_t)(c_len - 2U));
            s_frame_c_buf[c_len++] = (uint8_t)(c_crc >> 8);
            s_frame_c_buf[c_len++] = (uint8_t)(c_crc & 0xFFU);
            /* c_len is now 54: 6 header + 46 payload + 2 CRC — fits s_frame_c_buf[60]. */

            /* Append Frame C directly after Frame A in the TX buffer.
             * Buf_Telemetry_UART4[512] has headroom: max frame is B at ~326 B + 7 header = 333 B.
             * A(48 B) + C(54 B) = 102 B — well within 512 B. */
            {
                uint16_t j;
                for (j = 0; j < c_len; j++) {
                    Buf_Telemetry_UART4[len + j] = s_frame_c_buf[j];
                }
                len += c_len;
            }
        }
    }
    else // 20Hz Frame B
    {
        // FRAME B �� same 16-bit payload LEN as Frame A
        // MRAC: 4 axes * (MAX_NUM_BASIS + 2) floats
        // PID: 12 loops * 3 floats = 36 floats
        // Tail: u8 + 3f + f + f + u8 + f(real_voltage) = 26 bytes
        uint8_t total_floats = 4 * (MAX_NUM_BASIS + 2) + 36;
        uint16_t payload_len = (uint16_t)((uint16_t)total_floats * 4U + 26U);
        Buf_Telemetry_UART4[2] = 0x02; // ID
        Buf_Telemetry_UART4[3] = (uint8_t)(payload_len >> 8);
        Buf_Telemetry_UART4[4] = (uint8_t)(payload_len & 0xFFU);
        Buf_Telemetry_UART4[5] = MAX_NUM_BASIS;
        len = 6;
        
        // 4 Axes MRAC
        MRAC_AxisState_t* axes[4] = {&mrac_state.pitch, &mrac_state.roll, &mrac_state.yaw, &mrac_state.z_rate};
        for(int ax = 0; ax < 4; ax++) {
            for(i = 0; i < MAX_NUM_BASIS; i++) {
                Buf_Telemetry_UART4[len++] = BYTE0(axes[ax]->Theta[i]);
                Buf_Telemetry_UART4[len++] = BYTE1(axes[ax]->Theta[i]);
                Buf_Telemetry_UART4[len++] = BYTE2(axes[ax]->Theta[i]);
                Buf_Telemetry_UART4[len++] = BYTE3(axes[ax]->Theta[i]);
            }
            Buf_Telemetry_UART4[len++] = BYTE0(axes[ax]->u_nom);
            Buf_Telemetry_UART4[len++] = BYTE1(axes[ax]->u_nom);
            Buf_Telemetry_UART4[len++] = BYTE2(axes[ax]->u_nom);
            Buf_Telemetry_UART4[len++] = BYTE3(axes[ax]->u_nom);
            
            Buf_Telemetry_UART4[len++] = BYTE0(axes[ax]->xm);
            Buf_Telemetry_UART4[len++] = BYTE1(axes[ax]->xm);
            Buf_Telemetry_UART4[len++] = BYTE2(axes[ax]->xm);
            Buf_Telemetry_UART4[len++] = BYTE3(axes[ax]->xm);
        }
        
        // 12 PID loops (adds altitude Z_pos + horizontal velocity)
        PIDTypeDef* pids[12] = {&Ctrler.pitchPID, &Ctrler.rollPID, &Ctrler.yawPID, 
                               &Ctrler.gyroxPID, &Ctrler.gyroyPID, &Ctrler.gyrozPID, 
                               &Ctrler.Z_ratePID, &Ctrler.locxPID, &Ctrler.locyPID,
                               &Ctrler.Z_posPID, &Ctrler.locxsPID, &Ctrler.locysPID};
        for(i = 0; i < 12; i++) {
            Buf_Telemetry_UART4[len++] = BYTE0(pids[i]->FB);
            Buf_Telemetry_UART4[len++] = BYTE1(pids[i]->FB);
            Buf_Telemetry_UART4[len++] = BYTE2(pids[i]->FB);
            Buf_Telemetry_UART4[len++] = BYTE3(pids[i]->FB);
            
            Buf_Telemetry_UART4[len++] = BYTE0(pids[i]->Des);
            Buf_Telemetry_UART4[len++] = BYTE1(pids[i]->Des);
            Buf_Telemetry_UART4[len++] = BYTE2(pids[i]->Des);
            Buf_Telemetry_UART4[len++] = BYTE3(pids[i]->Des);
            
            Buf_Telemetry_UART4[len++] = BYTE0(pids[i]->U);
            Buf_Telemetry_UART4[len++] = BYTE1(pids[i]->U);
            Buf_Telemetry_UART4[len++] = BYTE2(pids[i]->U);
            Buf_Telemetry_UART4[len++] = BYTE3(pids[i]->U);
        }

        {
            uint8_t active_path_mode = 0;
            if (sinusoid_path.active) {
                active_path_mode = 2;
            } else if (circle_path.active) {
                active_path_mode = 3;
            } else if (figure8_path.active) {
                active_path_mode = 4;
            } else if (TWC.execute) {
                active_path_mode = 1;
            }
            Buf_Telemetry_UART4[len++] = active_path_mode;

            {
                float tf = TWC.target_x;
                Buf_Telemetry_UART4[len++] = BYTE0(tf);
                Buf_Telemetry_UART4[len++] = BYTE1(tf);
                Buf_Telemetry_UART4[len++] = BYTE2(tf);
                Buf_Telemetry_UART4[len++] = BYTE3(tf);
            }
            {
                float tf = TWC.target_y;
                Buf_Telemetry_UART4[len++] = BYTE0(tf);
                Buf_Telemetry_UART4[len++] = BYTE1(tf);
                Buf_Telemetry_UART4[len++] = BYTE2(tf);
                Buf_Telemetry_UART4[len++] = BYTE3(tf);
            }
            {
                float tf = TWC.target_z;
                Buf_Telemetry_UART4[len++] = BYTE0(tf);
                Buf_Telemetry_UART4[len++] = BYTE1(tf);
                Buf_Telemetry_UART4[len++] = BYTE2(tf);
                Buf_Telemetry_UART4[len++] = BYTE3(tf);
            }
            {
                float tf = sinusoid_path.t_elapsed;
                Buf_Telemetry_UART4[len++] = BYTE0(tf);
                Buf_Telemetry_UART4[len++] = BYTE1(tf);
                Buf_Telemetry_UART4[len++] = BYTE2(tf);
                Buf_Telemetry_UART4[len++] = BYTE3(tf);
            }
            {
                float tf = circle_path.theta;
                Buf_Telemetry_UART4[len++] = BYTE0(tf);
                Buf_Telemetry_UART4[len++] = BYTE1(tf);
                Buf_Telemetry_UART4[len++] = BYTE2(tf);
                Buf_Telemetry_UART4[len++] = BYTE3(tf);
            }
            Buf_Telemetry_UART4[len++] = TWC_arrived;
            {
                float tf = real_voltage;   /* battery pack voltage (4S), logged as status.vbat */
                Buf_Telemetry_UART4[len++] = BYTE0(tf);
                Buf_Telemetry_UART4[len++] = BYTE1(tf);
                Buf_Telemetry_UART4[len++] = BYTE2(tf);
                Buf_Telemetry_UART4[len++] = BYTE3(tf);
            }
        }
    }
    
    // CONSTRAINT: CRC coverage must match the host parser exactly.
    // WHY: Any mismatch causes silent frame drops in serial_bridge.
    /* CRC8 XOR over all bytes after sync: frame type, 16-bit LEN, MAX_NUM_BASIS, payload (index 2 .. len-1).
     * Skipped when the buffer already carries its own checksum(s) — i.e. the Frame A + Frame C
     * path, where Frame A closed its own CRC8 above and Frame C carries its own CRC16. */
    if (!frame_self_crc) {
        crc = 0;
        for (i = 2; i < len; i++) {
            crc ^= Buf_Telemetry_UART4[i];
        }
        Buf_Telemetry_UART4[len++] = crc;
    }
    
    frame_counter++;
    
    // DMA transfer on UART5 wireless link (DMA1_Stream7)
    DMA_Cmd(DMA1_Stream7, DISABLE);
    {
        uint32_t timeout = 1000U;
        while ((DMA_GetCmdStatus(DMA1_Stream7) == ENABLE) && (--timeout > 0U)) { }
    }

    /* Clear ALL stream-7 event flags before re-enabling, not just TCIF7. The
     * larger back-to-back Frame A+C burst latches a (benign) FIFO-error FEIF7
     * on the direct-mode stream; per RM0090 every event flag must be cleared
     * before EN is set again, so clearing only TCIF7 is fragile. Defensive —
     * lets the stream self-heal from any transient DMA error. */
    DMA_ClearFlag(DMA1_Stream7, DMA_FLAG_TCIF7 | DMA_FLAG_HTIF7 | DMA_FLAG_TEIF7
                              | DMA_FLAG_DMEIF7 | DMA_FLAG_FEIF7);

    DMA1_Stream7->M0AR = (uint32_t)&Buf_Telemetry_UART4;
    DMA1_Stream7->NDTR = len;
    DMA_Cmd(DMA1_Stream7, ENABLE);
    SendProf_Record(&g_send_prof.sec_telem_build, t_prof_sec);

    /* Streaming subscription (CMD 0x21). Ticked BEFORE the request handler so
     * a subscription accepted this cycle starts emitting on the next one,
     * rather than firing a data frame on the heels of its own schema reply.
     * No-op when no stream is active, so the default cadence is unchanged.
     * When the stream is routed to USART3 this touches DMA1_Stream3 only and
     * never waits on the UART5 telemetry burst above. */
    t_prof_sec = DWT->CYCCNT;
    Subscribe_StreamTick();
    SendProf_Record(&g_send_prof.sec_subscribe_tick, t_prof_sec);

    /* ADR-0011 follow-up (uart5_address_subscription_cmd): if a UART5
     * subscribe request has been staged by the IRQ-side parser (BSP/usart5.c),
     * build the 0x07 / 0x7F reply and emit it on a *second* DMA1_Stream7
     * turn. The reply observes the same UART5 timing contract as A/B
     * telemetry; no new FreeRTOS task is created, and the live telemetry
     * cadence is unchanged when no request is pending. */
    if (UA5RxSubscribePending != 0U) {
        t_prof_sec = DWT->CYCCNT;
        Uart5_Subscribe_HandleRequest();
        SendProf_Record(&g_send_prof.sec_uart5_request, t_prof_sec);
    }
}

extern volatile GS_Cmd_t gs_cmd_queue[16];
extern volatile uint8_t gs_cmd_head;
extern volatile uint8_t gs_cmd_tail;

static uint8_t s_transaction_result_buf[64];
static uint16_t s_transaction_history[16];
static uint8_t s_transaction_history_head = 0U;

static uint8_t TransactionWasSeen(uint16_t transaction_id)
{
    uint8_t i;
    for (i = 0U; i < 16U; i++) {
        if (s_transaction_history[i] == transaction_id) {
            return 1U;
        }
    }
    return 0U;
}

static void RememberTransaction(uint16_t transaction_id)
{
    s_transaction_history[s_transaction_history_head] = transaction_id;
    s_transaction_history_head = (uint8_t)((s_transaction_history_head + 1U) % 16U);
}

static uint8_t CommandSafetyReject(uint8_t id)
{
    if ((id == 0U) || (id > 0x1EU)) {
        return 4U; /* unknown command */
    }
    if ((id == 0x06U) && (DroneStatus.FlyMode != FlyMode_SDK)) {
        return 6U; /* safety interlock */
    }
    if (((id == 0x0AU) || (id == 0x0BU) || (id == 0x0CU) ||
         (id == 0x11U)) && (DroneStatus.FlyMode != FlyMode_SDK)) {
        return 6U;
    }
    if ((id == 0x18U) &&
        ((flight_phase != FLIGHT_PHASE_GROUND_IDLE) ||
         (DroneStatus.ARM_Status != DisArmed))) {
        return 6U;
    }
    return 0U;
}

static void SendTransactionResult(uint16_t transaction_id, uint8_t transport,
                                  uint8_t outcome, uint8_t command_id,
                                  uint8_t index, uint8_t reason,
                                  const char* detail)
{
    uint16_t frame_len = 0U;
    if ((transaction_id == 0U) ||
        (PlatformCommand_BuildResult(transaction_id, outcome, command_id, index,
                                     reason, detail, s_transaction_result_buf,
                                     (uint16_t)sizeof(s_transaction_result_buf),
                                     &frame_len) == 0U)) {
        return;
    }
    if (transport == SUBSCRIBE_RX_TRANSPORT_USART3) {
        (void)Usart3_Stream_TxSend(s_transaction_result_buf, frame_len);
    } else {
        Uart5_Subscribe_TxSend(s_transaction_result_buf, frame_len);
    }
}

/* Stop TWC / sinusoid / circle, neutral sticks, clear GS mission trigger, dangerous stop */
static void GroundStation_AbortAllPaths(void)
{
    // ARCH: Centralized abort keeps all stop paths synchronized.
    TWC.execute = 0;
    sinusoid_path.active = 0U;
    circle_path.active = 0U;
    figure8_path.active = 0U;
    RCInput_SetAuthority(0U);
    GS_KeySDKflag = 0U;
    FlightFSM_Event(FLIGHT_EVENT_DANGEROUS_STOP);
}

void Process_GroundStation_Command(void)
{
    while (gs_cmd_tail != gs_cmd_head)
    {
        uint8_t id = gs_cmd_queue[gs_cmd_tail].id;
        uint8_t idx = gs_cmd_queue[gs_cmd_tail].index;
        float val = gs_cmd_queue[gs_cmd_tail].value;
        uint16_t transaction_id = gs_cmd_queue[gs_cmd_tail].transaction_id;
        uint8_t transaction_flags = gs_cmd_queue[gs_cmd_tail].transaction_flags;
        uint8_t transaction_transport = gs_cmd_queue[gs_cmd_tail].transaction_transport;
        uint8_t reject_reason = CommandSafetyReject(id);

        /* CMD 0x1E idx=0 (estimator mode switch) is disarmed-only */
        if ((reject_reason == 0U) && (id == 0x1EU) && (idx == 0U) &&
            (DroneStatus.ARM_Status != DisArmed)) {
            reject_reason = 6U;
        }
        gs_cmd_tail = (gs_cmd_tail + 1) % 16;

        (void)transaction_flags;
        if (transaction_id != 0U) {
            if (TransactionWasSeen(transaction_id) != 0U) {
                SendTransactionResult(transaction_id, transaction_transport,
                                      PLATFORM_RESULT_APPLIED, id, idx, 0U,
                                      "duplicate");
                continue;
            }
            if (reject_reason != 0U) {
                SendTransactionResult(transaction_id, transaction_transport,
                                      PLATFORM_RESULT_REJECTED, id, idx,
                                      reject_reason, "command rejected");
                continue;
            }
            SendTransactionResult(transaction_id, transaction_transport,
                                  PLATFORM_RESULT_ACK, id, idx, 0U, "queued");
            RememberTransaction(transaction_id);
        }
        
        // CMD 0x01 �� PID gain update
        // INDEX encodes axis+gain: (axis 0-6). (gain 0=Kp, 1=Ki, 2=Kd)
        if (id == 0x01) {
            uint8_t axis = idx / 3;
            uint8_t gain = idx % 3;
            
            PIDTypeDef* pids[7];
            pids[0] = &Ctrler.pitchPID;
            pids[1] = &Ctrler.rollPID;
            pids[2] = &Ctrler.yawPID;
            pids[3] = &Ctrler.gyroxPID;
            pids[4] = &Ctrler.gyroyPID;
            pids[5] = &Ctrler.gyrozPID;
            pids[6] = &Ctrler.Z_ratePID;
                                   
            if (axis < 7 && val >= 0.0f && val <= 200.0f) {
                if (gain == 0) pids[axis]->Kp = val;
                else if (gain == 1) pids[axis]->Ki = val;
                else if (gain == 2) pids[axis]->Kd = val;
            }
        }
        
        // CMD 0x02 / 0x05 / 0x08 �� MRAC array element update (What_tol moved from 0x06 to 0x08; 0x06 = virtual RC)
        // High nibble: axis (0-3). Low nibble: element index.
        else if (id == 0x02 || id == 0x05 || id == 0x08) {
            uint8_t axis = (idx >> 4) & 0x0F;
            uint8_t elem = idx & 0x0F;
            
            MRAC_AxisConfig_t* configs[4];
            configs[0] = &mrac_config_pitch;
            configs[1] = &mrac_config_roll;
            configs[2] = &mrac_config_yaw;
            configs[3] = &mrac_config_z;
            
            if (axis < 4 && elem < MAX_NUM_BASIS) {
                if      (id == 0x02 && val >  0.0f) configs[axis]->gamma[elem]      = val;
                /* The projection only divides by What_tol inside the
                 * (What_limit - What_tol) band, so a limit below tol inverts it.
                 * elem 0 also keeps the symmetric lower bound MRAC_Init sets. */
                else if (id == 0x05 && val >= configs[axis]->What_tol[elem]) {
                    configs[axis]->What_limit[elem] = val;
                    if (elem == 0) configs[axis]->What_lower_limit[0] = -val;
                }
                else if (id == 0x08 && val >= 0.0f &&
                         val <= configs[axis]->What_limit[elem])
                    configs[axis]->What_tol[elem] = val;
            }
        }

        // CMD 0x06 — virtual stick injection. val is normalised [-1.0, +1.0]. idx: [0]=thr,[1]=pitch,[2]=roll,[3]=yaw.
        // Gate: FlyMode_SDK only (physical RC mode switch is still the hard kill via Check_Fly_Mode).
        // sbus_lost is NOT checked: RC stays ON as emergency fallback; authority flag in RCInput routes the signal.
        else if (id == 0x06) {
            if (DroneStatus.FlyMode == FlyMode_SDK && idx < 4) {
                float v = val;
                if (v >  1.0f) v =  1.0f;
                if (v < -1.0f) v = -1.0f;
                RCInput_SetVirtualStick((RC_Axis_t)idx, v);
            }
        }

        // CMD 0x07 — bench test mode (prop-wash safety): index 0 value > 0 enables.
        // This flag BLOCKS the automatic FLYING transition (4DOF fixture prop wash guard).
        // Does NOT cap throttle — use CMD 0x08 for independent throttle capping.
        else if (id == 0x07) {
            if (idx == 0) {
                bench_mode_active = (val > 0.0f) ? 1U : 0U;
            }
        }

        
        // CMD 0x03 �� Mixer/saturation update
        else if (id == 0x03) {
            MRAC_AxisConfig_t* configs[4];
            configs[0] = &mrac_config_pitch;
            configs[1] = &mrac_config_roll;
            configs[2] = &mrac_config_yaw;
            configs[3] = &mrac_config_z;
            if (idx < 4) {
                if (val > 1.0f) configs[idx]->mrac_to_mixer = val;
            } else if (idx < 8) {
                configs[idx - 4]->u_max = val;
            } else if (idx == 8) {
                if (val >= 0.0f && val <= 1.0f) gs_throttle_min_pct = val;
            } else if (idx == 9) {
                if (val >= 0.50f && val <= 1.0f) gs_throttle_max_pct = val;
            }
        }

        // CMD 0x09 �� velocity / angle safety limits (ground station)
        else if (id == 0x09) {
            if (idx == 0) {
                if (val > 0.05f && val < 20.0f) gs_max_horizontal_speed_mps = val;
            } else if (idx == 1) {
                if (val > 0.05f && val < 10.0f) gs_max_vertical_speed_mps = val;
            } else if (idx == 2) {
                if (val >= 3.0f && val <= 60.0f) gs_max_pitch_deg = val;
            } else if (idx == 3) {
                if (val >= 3.0f && val <= 60.0f) gs_max_roll_deg = val;
            }
        }
        
        // CMD 0x04 �� Flight mode (idx 0 = dangerous stop + path abort)
        else if (id == 0x04) {
            if (idx == 0) {
                GroundStation_AbortAllPaths();
                GS_KeySDKflag = 0U;
            } else if (idx == 1) {
                FlightFSM_Event(FLIGHT_EVENT_RECOVER_SDK);
            }
        }

        /* CMD 0x0A �� TWC target (point-to-point); only in SDK mode */
        else if (id == 0x0A) {
            if (DroneStatus.FlyMode != FlyMode_SDK) {
                /* ignore */
            } else if (idx == 0) {
                TWC.target_x = val;
            } else if (idx == 1) {
                TWC.target_y = val;
            } else if (idx == 2) {
                TWC.target_z = val;
            } else if (idx == 3) {
                TWC.set_yaw = val;
            } else if (idx == 4) {
                TWC.execute = ((uint8_t)(val + 0.5f) != 0) ? 1 : 0;
            }
        }

        /* CMD 0x0B �� sinusoidal path parameters (FlyMode_SDK only) */
        else if (id == 0x0B) {
            if (DroneStatus.FlyMode != FlyMode_SDK) {
                /* ignore */
            } else if (idx == 0) {
                sinusoid_path.center_x = val;
            } else if (idx == 1) {
                sinusoid_path.center_y = val;
            } else if (idx == 2) {
                sinusoid_path.center_z = val;
            } else if (idx == 3) {
                sinusoid_path.amplitude = val;
            } else if (idx == 4) {
                sinusoid_path.frequency = val;
            } else if (idx == 5) {
                sinusoid_path.duration = val;
            } else if (idx == 6) {
                sinusoid_path.axis = (uint8_t)(val + 0.5f);
                if (sinusoid_path.axis > 2U) {
                    sinusoid_path.axis = 2U;
                }
            } else if (idx == 7) {
                if (((uint8_t)(val + 0.5f)) != 0) {
                    taskENTER_CRITICAL();
                    sinusoid_path.active = 1U;
                    sinusoid_path.t_elapsed = 0.0f;
                    AutoflyTask_WaypointReset();
                    taskEXIT_CRITICAL();
                } else {
                    sinusoid_path.active = 0U;
                }
            }
        }

        /* CMD 0x0C �� circle path (FlyMode_SDK only) */
        else if (id == 0x0C) {
            if (DroneStatus.FlyMode != FlyMode_SDK) {
                /* ignore */
            } else if (idx == 0) {
                circle_path.center_x = val;
            } else if (idx == 1) {
                circle_path.center_y = val;
            } else if (idx == 2) {
                circle_path.center_z = val;
            } else if (idx == 3) {
                circle_path.radius = val;
            } else if (idx == 4) {
                circle_path.angular_speed = val;
            } else if (idx == 5) {
                circle_path.duration = val;
            } else if (idx == 6) {
                if (((uint8_t)(val + 0.5f)) != 0) {
                    taskENTER_CRITICAL();
                    circle_path.active = 1U;
                    circle_path.theta = 0.0f;
                    circle_path.t_elapsed = 0.0f;
                    AutoflyTask_WaypointReset();
                    taskEXIT_CRITICAL();
                } else {
                    circle_path.active = 0U;
                }
            }
        }

        /* CMD 0x11 - figure-8 (lemniscate) path (FlyMode_SDK only) */
        else if (id == 0x11) {
            if (DroneStatus.FlyMode != FlyMode_SDK) {
                /* ignore */
            } else if (idx == 0) {
                figure8_path.center_x = val;
            } else if (idx == 1) {
                figure8_path.center_y = val;
            } else if (idx == 2) {
                figure8_path.center_z = val;
            } else if (idx == 3) {
                figure8_path.amplitude = val;
            } else if (idx == 4) {
                figure8_path.angular_speed = val;
            } else if (idx == 5) {
                figure8_path.duration = val;
            } else if (idx == 6) {
                figure8_path.type = (uint8_t)(val + 0.5f);
                if (figure8_path.type > 1U) {
                    figure8_path.type = 1U;
                }
            } else if (idx == 7) {
                if (((uint8_t)(val + 0.5f)) != 0) {
                    taskENTER_CRITICAL();
                    figure8_path.active = 1U;
                    figure8_path.theta = 0.0f;
                    figure8_path.t_elapsed = 0.0f;
                    AutoflyTask_WaypointReset();
                    taskEXIT_CRITICAL();
                } else {
                    figure8_path.active = 0U;
                }
            }
        }

        /* CMD 0x12 - shared waypoint-density spacing (loc-PID units = cm; GUI sends Δs_m*100); 0 = continuous */
        else if (id == 0x12) {
            if (idx == 0) {
                waypoint_spacing = (val < 0.0f) ? 0.0f : val;
                AutoflyTask_WaypointReset();
            }
        }

        /* CMD 0x0D �� abort all paths + neutral sticks + dangerous stop */
        else if (id == 0x0D) {
            if (idx == 0) {
                GroundStation_AbortAllPaths();
            }
        }

        /* CMD 0x0F — multiplexed ground-station command (telemetry-throughput-2026-09-08).
         *
         * Two uses live behind this id, disambiguated by idx:
         *   idx 0..12    MRAC feature-flag runtime toggle (val >= 0.5 = ON, else OFF).
         *                0=adaptation_on  1=projection_on  2=deadzone_on  3=hard_freeze_on
         *                4=tanh_saturation_on  5=e_modification_on  6=l1_filtering_on
         *                7=axis_enable_pitch  8=axis_enable_roll  9=axis_enable_yaw
         *                10=output_injection_on (shadow-mode gate: 0=motors see pure PID)
         *                11=id_frame_on (high-rate system-ID frame 0x03 @100Hz, replaces A/B)
         *                12=of_frame_on (OF calibration/fusion frame 0x05 @200Hz, replaces A/B)
         *   idx 100..102 Telemetry mode switch (val ignored). idx 100=LEGACY 101=MIXED
         *                102=SUBSCRIBE_ONLY. idx 13..99 and idx > 102 reserved.
         *
         * The previous implementation had two parallel `else if (id == 0x0F)` branches
         * in this dispatch chain; the MRAC branch sat earlier in the file and shadowed
         * the telemetry branch, making the telemetry-mode switch dead code. The sentinel
         * split keeps the wire shape (9 B, idx+val+CRC8) unchanged and is backward
         * compatible with any host that was only sending idx 0..12. */
        else if (id == 0x0F) {
            if (idx <= 12U) {
                uint8_t on = ((uint8_t)(val + 0.5f)) != 0U ? 1U : 0U;
                switch (idx) {
                    case 0:  mrac_flags.adaptation_on      = on; break;
                    case 1:  mrac_flags.projection_on      = on; break;
                    case 2:  mrac_flags.deadzone_on        = on; break;
                    case 3:  mrac_flags.hard_freeze_on     = on; break;
                    case 4:  mrac_flags.tanh_saturation_on = on; break;
                    case 5:  mrac_flags.e_modification_on  = on; break;
                    case 6:  mrac_flags.l1_filtering_on    = on; break;
                    case 7:  mrac_flags.axis_enable_pitch  = on; break;
                    case 8:  mrac_flags.axis_enable_roll   = on; break;
                    case 9:  mrac_flags.axis_enable_yaw    = on; break;
                    case 10: mrac_flags.output_injection_on = on; break;
                    case 11: mrac_flags.id_frame_on         = on; break;
                    case 12: mrac_flags.of_frame_on         = on; break;
                    default: break;  /* unreachable; idx <= 12 already gated */
                }
            } else if ((idx >= 100U) && (idx <= 102U)) {
                /* idx 100=LEGACY 101=MIXED 102=SUBSCRIBE_ONLY. Val ignored.
                 * Default at boot is MIXED (SUBSCRIBE_DEFAULT_TELEMETRY_MODE).
                 * SUBSCRIBE_ONLY skips the UART4 DMA busy-wait so Send_Task runs at
                 * nominal 200 Hz instead of being paced to ~80 Hz by the 3.7 ms wait. */
                SetTelemetryMode((uint8_t)(idx - 100U));
            }
        }

        /* CMD 0x13 — reference model type selector (idx 0, val = 0/1/2).
         *   0 = passthrough (xm = r), 1 = first-order, 2 = second-order.
         * Snaps all reference states to plant on change for bumpless switching. */
        else if (id == 0x13) {
            if (idx == 0) {
                uint8_t t = (uint8_t)(val + 0.5f);
                if (t > 2U) t = 2U;
                mrac_flags.ref_model_type = t;
                mrac_state.pitch.xm  = mrac_state.pitch.x;   mrac_state.pitch.xm_dot  = 0.0f;
                mrac_state.roll.xm   = mrac_state.roll.x;    mrac_state.roll.xm_dot   = 0.0f;
                mrac_state.yaw.xm    = mrac_state.yaw.x;     mrac_state.yaw.xm_dot    = 0.0f;
                mrac_state.z_rate.xm = mrac_state.z_rate.x;  mrac_state.z_rate.xm_dot = 0.0f;
            }
        }

        /* CMD 0x14 — SysID excitation control (ADR-0004). Set params (idx 0-5) then start/abort (idx 6).
         *   idx 0=axis(0 pitch,1 roll,2 yaw,3 Z)  1=signal(0 chirp,1 multisine)  2=f0 Hz  3=f1 Hz
         *       4=amplitude (deg/s; Z in m/s)  5=duration s  6=start(>=0.5)/abort(<0.5)
         *       7=geofence enable (>=0.5 ON default, <0.5 OFF — pilot-watch override)
         * Dashboard sends CMD 0x10 (OF-origin reset) immediately before idx 6 start. */
        else if (id == 0x14) {
            static uint8_t sx_axis = 0U, sx_sig = 0U;
            static float sx_f0 = 1.0f, sx_f1 = 12.0f, sx_amp = 30.0f, sx_dur = 20.0f;
            switch (idx) {
                case 0: sx_axis = (uint8_t)(val + 0.5f); break;
                case 1: sx_sig  = (uint8_t)(val + 0.5f); break;
                case 2: sx_f0 = val; break;
                case 3: sx_f1 = val; break;
                case 4: sx_amp = val; break;
                case 5: sx_dur = val; break;
                case 6:
                    if (((uint8_t)(val + 0.5f)) != 0U) {
                        /* Self-sufficient OF-origin reset (ADR-0004 dec.6/finding #13): do not rely
                         * on the GS having sent CMD 0x10 first, so the green-zone centre is always
                         * captured at a fresh (0,0) origin. Mirrors the 0x10 handler below. */
                        ano_of.earth_x       = 0.0f;
                        ano_of.earth_y       = 0.0f;
                        ano_of.earth_x_ture  = 0.0f;
                        ano_of.earth_y_ture  = 0.0f;
                        ano_of.DISTANCE_X    = 0.0f;
                        ano_of.DISTANCE_Y    = 0.0f;
                        Ctrler.locxPID.FB    = 0.0f;
                        Ctrler.locyPID.FB    = 0.0f;
                        Ctrler.locxPID.Des   = 0.0f;
                        Ctrler.locyPID.Des   = 0.0f;
                        Ctrler.locxsPID.Des  = 0.0f;
                        Ctrler.locysPID.Des  = 0.0f;
                        SysID_Start((SysID_Axis_e)sx_axis, (SysID_Signal_e)sx_sig, sx_f0, sx_f1, sx_amp, sx_dur);
                    } else {
                        SysID_Abort();
                    }
                    break;
                case 7: SysID_SetGeofence(((uint8_t)(val + 0.5f)) != 0U ? 1U : 0U); break;
                default: break;
            }
        }

        /* CMD 0x15 — gyro low-pass filter (Phase 1, ADR-0004).
         *   idx 0 = enable (val>=0.5 ON, else pass-through)
         *   idx 1 = cutoff Hz (applied to pitch/roll/yaw)  */
        else if (id == 0x15) {
            if (idx == 0) {
                GyroFilter_SetEnabled(((uint8_t)(val + 0.5f)) != 0U ? 1U : 0U);
            } else if (idx == 1) {
                GyroFilter_SetCutoff(GYRO_FILT_PITCH, val);
                GyroFilter_SetCutoff(GYRO_FILT_ROLL,  val);
                GyroFilter_SetCutoff(GYRO_FILT_YAW,   val);
            }
        }

        /* CMD 0x10 — reset world-frame optical flow origin.
         * Zeros accumulated earth_x/y position so the drone's current location
         * becomes the new (0, 0) world origin.  Also syncs position setpoints
         * to avoid a sudden jump on the next control tick. */
        else if (id == 0x10) {
            if (idx == 0) {
                Reset_World_Origin();
            }
        }

        /* CMD 0x17 — one-shot optical-flow velocity-bias capture.
         * Pilot places the drone level and still, then triggers this; the stabilizer
         * task averages of2_dx_fix/dy_fix over ~2 s and stores the bias (streamed back
         * as of.bias_x/y in the 0x05 frame so the capture can be confirmed). Fixes the
         * unbounded earth_x/y drift (~25 m/200 s) caused by the un-subtracted DC bias. */
        else if (id == 0x17) {
            if (idx == 0) {
                g_of_bias_capture_req = 1U;
            }
        }

        /* CMD 0x1E — OF bias estimation mode selector.
         *   idx=0: set mode (val=0 FIXED, val=1 EMA, val=2 EKF)
         *          DISARMED ONLY: mode switch is rejected when armed
         *          (safety gate mirrors CMD 0x18 and motor bench gate).
         *          Firmware falls back to FIXED automatically on EKF
         *          health failure (g_ekf_of_fallback set sticky).
         *   idx=1: set EMA freeze flag (val=0 run, val=1 freeze)
         *          Allowed any time (freeze = safe, no control impact).
         *   idx=2: set EMA time constant tau (seconds).
         *          Clamped to [1.0, 300.0] s. Allowed any time.
         *          Larger tau → slower tracking, less pull-back.
         *
         * All globals are volatile and DWARF-subscribable. */
        else if (id == 0x1E) {
            if (idx == 0) {
                /* Mode change — DISARMED ONLY */
                if (DroneStatus.ARM_Status != DisArmed) {
                    /* reject: drone is armed — do not change estimator mid-flight */
                } else {
                    if (val >= 2.0f)       g_of_bias_mode = 2U;
                    else if (val >= 1.0f)  g_of_bias_mode = 1U;
                    else                   g_of_bias_mode = 0U;
                }
            } else if (idx == 1) {
                g_of_bias_ema_freeze = (val >= 0.5f) ? 1U : 0U;
            } else if (idx == 2) {
                /* Tau clamp: [1.0, 300.0] s */
                if (val < 1.0f)   val = 1.0f;
                if (val > 300.0f) val = 300.0f;
                g_of_bias_ema_tau_s = val;
            }
        }

        /* CMD 0x18 — force recalibration (ADR-0011).
         * Re-enters cold-cal from the top. Accepted only in GROUND_IDLE and DisArmed.
         * Resets: s_cal_trim, s_cal_hot, g_cal_health, g_estimator_ready, EKF. */
        else if (id == 0x18) {
            if (idx == 0) {
                if (flight_phase != FLIGHT_PHASE_GROUND_IDLE ||
                    DroneStatus.ARM_Status != DisArmed) {
                    /* refused: not in pre-flight ground-idle state */
                } else {
                    /* Reset accel bias to zero */
                    s_cal_trim.b_a[0] = 0.0f;
                    s_cal_trim.b_a[1] = 0.0f;
                    s_cal_trim.b_a[2] = 0.0f;
                    s_cal_trim.state = CAL_TRIM_STATE_WAIT_TAKEOFF;
                    s_cal_trim.run_ticks = 0U;
                    s_cal_trim.settled_ticks = 0U;
                    /* Reset gyro bias to zero */
                    s_cal_hot.b_g[0] = 0.0f;
                    s_cal_hot.b_g[1] = 0.0f;
                    s_cal_hot.b_g[2] = 0.0f;
                    s_cal_hot.state = CAL_HOT_STATE_WAIT_STILL;
                    s_cal_hot.still_tick = 0U;
                    s_cal_hot.acc_tick = 0U;
                    s_cal_hot.rejected = 0U;
                    s_cal_hot.cleared = 1U;
                    /* Clear health flags but preserve MANUAL_ORIGIN_RESET (0x80) */
                    g_cal_health = 0x80U;   /* MANUAL_ORIGIN_RESET sticky */
                    /* Force cold cal to re-run from top */
                    g_estimator_ready = 0U;
                    /* Re-init EKF */
                    if (s_ekf_inited) {
                        Ekf9_Init(&s_ekf, EKF_RUN_ENABLED);
                    }
                }
            }
        }

        /* CMD 0x0E - ground-station SDK arm switch */
        else if (id == 0x0E) {
            if (idx == 0) {
                if (((uint8_t)(val + 0.5f)) != 0) {
                    FlightFSM_Event(FLIGHT_EVENT_ARM_REQUEST);
                    if (FlightFSM_GetState() == FLIGHT_STATE_ARMED) {
                        GS_KeySDKflag = 1U;
                        RCInput_SetAuthority(1U);
                        /* If already airborne, override the -1.0f throttle floor that
                         * SetAuthority just set.  Without this, the motor-idle guard
                         * (Z_pos.FB < 0.3 && THR < -0.85) fires immediately and cuts
                         * motors mid-flight.  On the ground the floor stays at -1.0f
                         * so the pilot must raise the throttle slider deliberately. */
                        if (Ctrler.Z_posPID.FB > 0.35f) {
                            RCInput_SetVirtualStick(RC_AXIS_THR, 0.0f);
                        }
                    }
                } else {
                    /* ARM REQ OFF: relinquish PC authority only — drone stays ARMED.
                     * Physical RC resumes immediately so the pilot can land safely.
                     * DISARM_REQUEST is intentionally omitted: firing it mid-air cuts
                     * motors. Pilot disarms via RC stick gesture after landing. */
                    GS_KeySDKflag = 0U;
                    RCInput_SetAuthority(0U);
                }
            }
        }

        /* CMD 0x16 — motor bench test (DISARMED-only; thrust-stand experiment).
         *   idx 0 = enable / heartbeat (val>=0.5 ON; each send pets the dead-man)
         *   idx 1 = motor select (1..4 = M1..M4; 0 = none)
         *   idx 2 = commanded CCR (clamped to [2000,4000])
         * The stabilizer drives only the selected motor and zeroes everything if no
         * heartbeat arrives within MOTOR_TEST_DEADMAN_TICKS or the FSM leaves DISARMED.
         * See docs/bench_characterization.md. */
        else if (id == 0x16) {
            if (idx == 0) {
                if (((uint8_t)(val + 0.5f)) != 0U) {
                    motor_test_active   = 1U;
                    motor_test_watchdog = 0U;   /* heartbeat: pet the dead-man */
                } else {
                    motor_test_active = 0U;
                }
            } else if (idx == 1) {
                uint8_t m = (uint8_t)(val + 0.5f);
                motor_test_id = (m <= 4U) ? m : 0U;
            } else if (idx == 2) {
                float c = val;
                if (c < 2000.0f) c = 2000.0f;
                if (c > 4000.0f) c = 4000.0f;
                motor_test_ccr = (uint16_t)(c + 0.5f);
            }
        }

        if (transaction_id != 0U) {
            SendTransactionResult(transaction_id, transaction_transport,
                                  PLATFORM_RESULT_APPLIED, id, idx, 0U,
                                  "applied");
        }
    }
}

