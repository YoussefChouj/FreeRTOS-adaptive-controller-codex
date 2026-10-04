/**
 * @module     RemoterTask.c
 * @subsystem  input
 * @owner      Remoter_Task (USER/main.c, 10 ms): remoter_task scales the SBUS sticks and refreshes sbus_lost and the
 *             rc_input layer. Stabilizer_Task (TASK/StabilizerTask.c, 5 ms) calls Check_Fly_Mode once per tick.
 * @purpose    RC receiver front end: raw SBUS sticks to the legacy 3000 +/- 1000 stick scale (Remoter.*Ctrler),
 *             link-loss detection, stick gestures (arm, disarm, idle enable) and the switch channels (ch9 kill,
 *             ch5 land, ch7 fly-up, ch8 preset path) turned into flight-FSM events and trigger flags.
 * @inputs     sbus_channel[] and sbus_last_valid_tick (SBUS UART driver), FSM state, flight_phase,
 *             g_motor_idle_enabled.
 * @outputs    Remoter.{Pit,Rol,Thr,Yaw}Ctrler, sbus_lost, StickMotion counters, FlightFSM_Event calls,
 *             flight_phase / TWC.execute (ch5 land), sbus_flyup_trigger, sbus_path_trigger, RC authority (ch8).
 * The PROTECTED rc_kill / rc_ch5_land regions are listed in ground_station/flashtool/protected_set.yaml; the code
 * gate refuses agent diffs that touch them, so they are kept byte for byte.
 */

#include "RemoterTask.h"
#include "FreeRTOS.h"
#include "task.h"
#include "rc_input.h"
#include "flight_fsm.h"
/* WFB BEGIN glue */
#include "wfb_glue.h"
/* WFB END glue */

/* ------------------------------------------------------------------
 * Private constants
 * ------------------------------------------------------------------ */

/* Stick scaling: raw SBUS sticks run [200, 1800] around RC_SBUS_CENTRE; the rest of the firmware reads them on the
   legacy scale RC_STICK_CENTRE +/- RC_STICK_HALF_SPAN (rc_input.c RC_RAW_CENTER/RC_RAW_HALF_RANGE match it).
   RC_SBUS_CENTRE stays an int so the subtraction stays integer; the double constants keep the original arithmetic. */
#define RC_SBUS_CENTRE      1000
#define RC_SBUS_HALF_RANGE  800.0
#define RC_STICK_CENTRE     3000.0
#define RC_STICK_HALF_SPAN  1000.0
#define RC_STICK_VALID_MIN  1800      /* a scaled stick outside [MIN, MAX] is a glitch and reads as centre */
#define RC_STICK_VALID_MAX  4200
#define RC_LINK_TIMEOUT_MS  500       /* no valid SBUS frame for longer than this sets sbus_lost */
#define RC_AUX_HIGH         500U      /* ch7 / ch8 momentary switch reads high above this raw value */

/* Scaled stick to normalised [-1, 1] (float arithmetic, as the gesture thresholds below expect). */
#define RC_STICK_NORM(raw)  (((float)(raw) - (float)RC_STICK_CENTRE) / (float)RC_STICK_HALF_SPAN)

/* Stick gesture thresholds in normalised [-1, 1] units.
 * Former raw thresholds: MAX ~4000, MIN ~2000, MID ~3000 on [2000,4000] scale. */
#define is_Stick_MAX(value)      ( (value) >  0.75f )
#define is_Stick_MIN(value)      ( (value) < -0.75f )
#define is_Stick_MID(value)      ( (value) > -0.1f && (value) < 0.1f )

/* ------------------------------------------------------------------
 * Module state
 * ------------------------------------------------------------------ */

float channel[4];   /* scaled sticks: [0] roll, [1] pitch, [2] throttle, [3] yaw */

/* ------------------------------------------------------------------
 * Public API
 * ------------------------------------------------------------------ */

/* Remoter_Task body, every 10 ms: scale the four sticks, reject glitches, publish them to Remoter, refresh sbus_lost,
   then let rc_input capture the neutral and update its pilot/virtual stick view. */
void remoter_task(void)
{
    channel[0] = (sbus_channel[0] - RC_SBUS_CENTRE) / RC_SBUS_HALF_RANGE * RC_STICK_HALF_SPAN + RC_STICK_CENTRE;
    channel[1] = (sbus_channel[1] - RC_SBUS_CENTRE) / RC_SBUS_HALF_RANGE * RC_STICK_HALF_SPAN + RC_STICK_CENTRE;
    channel[2] = (sbus_channel[2] - RC_SBUS_CENTRE) / RC_SBUS_HALF_RANGE * RC_STICK_HALF_SPAN + RC_STICK_CENTRE;
    channel[3] = (sbus_channel[3] - RC_SBUS_CENTRE) / RC_SBUS_HALF_RANGE * RC_STICK_HALF_SPAN + RC_STICK_CENTRE;

    if (channel[0] < RC_STICK_VALID_MIN || channel[0] > RC_STICK_VALID_MAX) channel[0] = RC_STICK_CENTRE;
    if (channel[1] < RC_STICK_VALID_MIN || channel[1] > RC_STICK_VALID_MAX) channel[1] = RC_STICK_CENTRE;
    if (channel[2] < RC_STICK_VALID_MIN || channel[2] > RC_STICK_VALID_MAX) channel[2] = RC_STICK_CENTRE;
    if (channel[3] < RC_STICK_VALID_MIN || channel[3] > RC_STICK_VALID_MAX) channel[3] = RC_STICK_CENTRE;

    Remoter.PitCtrler = channel[1];
    Remoter.RolCtrler = channel[0];
    Remoter.ThrCtrler = channel[2];
    Remoter.YawCtrler = channel[3];

    /* Link loss: no valid frame since boot (after the first RC_LINK_TIMEOUT_MS), or none for RC_LINK_TIMEOUT_MS. */
    {
        TickType_t now = xTaskGetTickCount();
        if (sbus_last_valid_tick == 0U) {
            if (now > pdMS_TO_TICKS(RC_LINK_TIMEOUT_MS)) {
                sbus_lost = 1U;
            }
        } else {
            if ((now - sbus_last_valid_tick) > pdMS_TO_TICKS(RC_LINK_TIMEOUT_MS)) {
                sbus_lost = 1U;
            } else {
                sbus_lost = 0U;
            }
        }
    }

    RCInput_UpdateNeutral();
    RCInput_Update();
}

/* Count how many consecutive ticks each stick gesture is held, and fire arm / disarm / idle enable once a count
   reaches its *_Delay_time (Global_file/global_declare.h). */
void Check_Stick_Motion(void)
{
    /* Gesture recognition must read physical sticks, never virtual ones.
     * When authority=1 (IDLE mode), RCInput_Get() returns virtual[]=0 which
     * makes is_Stick_MAX(yaw) always false -> arm gesture never fires. */
    float eff_thr = RC_STICK_NORM(Remoter.ThrCtrler);
    float eff_pit = RC_STICK_NORM(Remoter.PitCtrler);
    float eff_rol = RC_STICK_NORM(Remoter.RolCtrler);
    float eff_yaw = RC_STICK_NORM(Remoter.YawCtrler);

    /* Left stick (throttle, yaw). */
    if (is_Stick_MIN(eff_thr) && is_Stick_MAX(eff_yaw))   /* arm */
        StickMotion.LeftStick_RightDown_cnt++;
    else StickMotion.LeftStick_RightDown_cnt = 0;

    if (is_Stick_MIN(eff_thr) && is_Stick_MIN(eff_yaw))   /* disarm */
        StickMotion.LeftStick_LeftDown_cnt++;
    else StickMotion.LeftStick_LeftDown_cnt = 0;

    if (is_Stick_MAX(eff_thr) && is_Stick_MIN(eff_yaw))   /* adjust: counted, no reader */
        StickMotion.LeftStick_LeftUp_cnt++;
    else StickMotion.LeftStick_LeftUp_cnt = 0;

    if (is_Stick_MAX(eff_thr) && is_Stick_MAX(eff_yaw))   /* debug: counted, no reader */
        StickMotion.LeftStick_RightUp_cnt++;
    else StickMotion.LeftStick_RightUp_cnt = 0;

    /* Right stick (pitch, roll). Only RightDown (idle enable) has a reader. */
    if (is_Stick_MIN(eff_pit) && is_Stick_MIN(eff_rol))
        StickMotion.RightStick_LeftDown_cnt++;
    else StickMotion.RightStick_LeftDown_cnt = 0;

    if (is_Stick_MIN(eff_pit) && is_Stick_MAX(eff_rol))   /* idle enable */
        StickMotion.RightStick_RightDown_cnt++;
    else StickMotion.RightStick_RightDown_cnt = 0;

    if (is_Stick_MAX(eff_pit) && is_Stick_MIN(eff_rol))
        StickMotion.RightStick_LeftUp_cnt++;
    else StickMotion.RightStick_LeftUp_cnt = 0;

    if (is_Stick_MAX(eff_pit) && is_Stick_MAX(eff_rol))
        StickMotion.RightStick_RightUp_cnt++;
    else StickMotion.RightStick_RightUp_cnt = 0;

    if (StickMotion.LeftStick_RightDown_cnt >= ARM_Delay_time)
    {
        FlightFSM_Event(FLIGHT_EVENT_ARM_REQUEST);
        StickMotion.LeftStick_RightDown_cnt = 0; StickMotion.LeftStick_LeftDown_cnt = 0;
    }
    if (StickMotion.LeftStick_LeftDown_cnt >= DISARM_Delay_time)
    {
        FlightFSM_Event(FLIGHT_EVENT_DISARM_REQUEST);
        StickMotion.LeftStick_RightDown_cnt = 0; StickMotion.LeftStick_LeftDown_cnt = 0;
    }

    /* Idle-enable gesture: RightStick bottom-right (pitch MIN + roll MAX) held
     * for IDLE_ENABLE_Delay_time ticks.  Accepted only when ARMED, GROUND_IDLE,
     * throttle stick low, and idle not already enabled.  This is the deliberate
     * second step after arming that allows motors to spin at idle RPM. */
    if (StickMotion.RightStick_RightDown_cnt >= IDLE_ENABLE_Delay_time)
    {
        if (FlightFSM_GetState() == FLIGHT_STATE_ARMED &&
            (flight_phase == FLIGHT_PHASE_GROUND_IDLE ||
             flight_phase == FLIGHT_PHASE_LANDED) &&
            is_Stick_MIN(eff_thr) &&
            !g_motor_idle_enabled)
        {
            /* Re-arming after a landing: the idle gesture returns LANDED to
             * GROUND_IDLE so takeoff detection runs again. */
            flight_phase = FLIGHT_PHASE_GROUND_IDLE;
            g_motor_idle_enabled = 1U;
        }
        StickMotion.RightStick_RightDown_cnt = 0;
    }
}

/* Stabilizer_Task, every 5 ms: stick gestures, then the RC failsafe (ch9 kill switch or link loss held for more than
   10 ticks fires DANGEROUS_STOP), then the rising edges of ch5 (land), ch7 (fly-up) and ch8 (preset path). */
void Check_Fly_Mode(void)
{
    static int DangerousStop_cnt = 0;
    Check_Stick_Motion();

	/* PROTECTED BEGIN rc_kill */
		/* P0 Finding 1: RC link loss (sbus_lost) must drive the failsafe too, not just ch9. */
		if( sbus_channel[9] <=500 || sbus_lost ) //���˲��������Ϸ�
	{
			DangerousStop_cnt ++;
	}
	else
	{
		DangerousStop_cnt = 0;
	}

	if(DangerousStop_cnt>10) //50ms
	{
		FlightFSM_Event(FLIGHT_EVENT_DANGEROUS_STOP);
	}
	else
	{
		FlightFSM_Event(FLIGHT_EVENT_RECOVER_SDK);
	}
	/* PROTECTED END rc_kill */

	/* PROTECTED BEGIN rc_ch5_land */
	/* --- SBUS ch5 (MODE_CH): rising edge into LAND position triggers landing.
	 * Only valid from FLYING phase — ignored when on ground (won't start ramp if not airborne).
	 * ch5 no longer sets authority; authority is managed by CMD 0x0E only.       */
	{
		static uint8_t ch5_prev = 0U;
		uint8_t ch5_now = (MODE_CH > 1300) ? 1U : 0U;
		if (ch5_now && !ch5_prev && flight_phase == FLIGHT_PHASE_FLYING)
		{
			/* WFB BEGIN glue */
			/* A ground-station flight lands through the glue (return to hover, settle, descend,
			 * the same function as CMD 0x1A idx 1); otherwise today's immediate LANDING. */
			uint8_t wfb_handled;
			taskENTER_CRITICAL();
			wfb_handled = wfb_glue_rc_land((uint32_t)xTaskGetTickCount() * (uint32_t)portTICK_PERIOD_MS);
			taskEXIT_CRITICAL();
			if (wfb_handled == 0U) {
			/* WFB END glue */
			flight_phase = FLIGHT_PHASE_LANDING;
			TWC.execute  = 0U;
			/* WFB BEGIN glue */
			}
			/* WFB END glue */
		}
		ch5_prev = ch5_now;
	}
	/* PROTECTED END rc_ch5_land */

    /* --- SBUS ch7 (FLYUP_CH): rising-edge fly-up to Z=0.5 m trigger -----------
     * Arms the drone if disarmed, then sets sbus_flyup_trigger.
     * Authority is released in StabilizerTask when the trigger is consumed.
     * Trigger is DROPPED (not pended) when idle is not enabled -- motors must
     * not spin without the explicit idle gesture. */
    {
        static uint8_t ch6_prev = 0U;
        uint8_t ch6_now = (FLYUP_CH > RC_AUX_HIGH) ? 1U : 0U;
        if (ch6_now && !ch6_prev)
        {
            if (FlightFSM_GetState() == FLIGHT_STATE_DISARMED)
                FlightFSM_Event(FLIGHT_EVENT_ARM_REQUEST);
            if (FlightFSM_GetState() == FLIGHT_STATE_ARMED &&
                g_motor_idle_enabled)
            {
                sbus_flyup_trigger = 1U;
            }
        }
        ch6_prev = ch6_now;
    }

    /* --- SBUS ch8 (PATH_EXEC_CH): rising-edge preset-path trigger --------------
     * Arms the drone (if needed) and sets sbus_path_trigger so the path
     * execution handler can launch the preset path loaded from the GS.
     * Trigger is DROPPED when idle is not enabled (same as ch7). */
    {
        static uint8_t ch8_prev = 0U;
        uint8_t ch8_now = (PATH_EXEC_CH > RC_AUX_HIGH) ? 1U : 0U;
        if (ch8_now && !ch8_prev)
        {
            if (FlightFSM_GetState() == FLIGHT_STATE_DISARMED)
            {
                FlightFSM_Event(FLIGHT_EVENT_ARM_REQUEST);
            }
            if (FlightFSM_GetState() == FLIGHT_STATE_ARMED &&
                g_motor_idle_enabled)
            {
                RCInput_SetAuthority(1U);
                sbus_path_trigger = 1U;
            }
        }
        ch8_prev = ch8_now;
    }
}
