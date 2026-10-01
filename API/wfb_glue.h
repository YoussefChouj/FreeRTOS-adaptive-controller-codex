#ifndef WFB_GLUE_H
#define WFB_GLUE_H

/* Workflow B integration glue (task F4): owns the trajectory buffer, the safety net and the
 * takeoff/land sequencer, decodes CMD 0x1A / 0x1B and turns the 200 Hz tick into firmware
 * requests. Pure C: no hardware, FreeRTOS or TASK/ header, so it builds on the host.
 * The TASK/ call sites fill wfb_glue_in_t, call under taskENTER_CRITICAL and apply
 * wfb_glue_out_t through the existing firmware paths (docs/workflow-b/interfaces.md). */

#include <stdint.h>
#include "wfb_types.h"

/* Result codes, same values as firmware/command_protocol.h:19-21 (not included: this header
 * stays hardware free). */
#define WFB_RESULT_ACK      0u
#define WFB_RESULT_REJECTED 1u
#define WFB_RESULT_APPLIED  2u

#define WFB_CMD_PRIM 0x1Au
#define WFB_CMD_TRAJ 0x1Bu

/* CMD 0x1A idx: flight primitives (interfaces.md section 1) */
#define WFB_PRIM_CMD_TAKEOFF     0u
#define WFB_PRIM_CMD_LAND        1u
#define WFB_PRIM_CMD_HEARTBEAT   2u
#define WFB_PRIM_CMD_SET_HOVER_Z 3u

/* CMD 0x1B idx: trajectory upload and control; BEGIN..COMMIT carry a payload */
#define WFB_TRAJ_CMD_BEGIN  0u
#define WFB_TRAJ_CMD_APPEND 1u
#define WFB_TRAJ_CMD_CRC_HI 2u
#define WFB_TRAJ_CMD_COMMIT 3u
#define WFB_TRAJ_CMD_START  4u
#define WFB_TRAJ_CMD_STOP   5u
#define WFB_TRAJ_CMD_CLEAR  6u

/* interfaces.md section 2; every field float so the telemetry decoder needs one type. */
typedef struct {
    float prim_state;
    float traj_state;
    float traj_n;
    float traj_rx;
    float traj_crc_hi;
    float traj_crc_lo;
    float traj_t;
    float last_err;
    float safety_trip;
    float hb_age;
    float gs_flight_active;
    float hover_z;
    float airborne_t;
} wfb_status_t;

/* Snapshot taken by the 200 Hz loop before wfb_glue_tick. Position in METRES, world frame. */
typedef struct {
    uint32_t now_ms;      /* FreeRTOS tick count x portTICK_PERIOD_MS; wraps, only differences used */
    float x_m;
    float y_m;
    float z_m;
    float roll_deg;
    float pitch_deg;
    float vbat_v;
    float yaw_deg;        /* heading in the TWC.set_yaw frame (Ctrler.yawPID.FB) */
    uint8_t armed;        /* FSM not DISARMED */
    uint8_t motors_idle;  /* motor idle enabled (the ch7 fly-up precondition) */
    uint8_t sbus_live;    /* physical RC link present */
    uint8_t airborne;     /* flight phase FLYING or LANDING */
    uint8_t rc_override;  /* physical roll/pitch stick active (the existing TWC cancel condition) */
} wfb_glue_in_t;

/* What the firmware must do this tick. */
typedef struct {
    uint8_t setpoint_valid;  /* drive TWC target (x/y m -> cm), TWC.set_yaw and execute = 1 */
    float x_sp_m;
    float y_sp_m;
    float z_sp_m;
    float yaw_sp_deg;        /* takeoff heading; the trajectory's yaw while one executes, then its last */
    uint8_t takeoff_req;     /* one-shot: the ch7 fly-up path (sbus_flyup_trigger = 1) */
    uint8_t land_req;        /* level: enter the existing LANDING phase (acted on only while FLYING) */
    uint8_t motor_stop_req;  /* level: safety KILL -> FLIGHT_EVENT_DANGEROUS_STOP */
} wfb_glue_out_t;

extern wfb_status_t g_wfb_status;

void    wfb_glue_init(void);
uint8_t wfb_glue_on_cmd(uint8_t cmd, uint8_t idx, float val, uint32_t now_ms); /* WFB_RESULT_* */
void    wfb_glue_tick(const wfb_glue_in_t *in, wfb_glue_out_t *out);            /* 200 Hz */
uint8_t wfb_glue_rc_land(uint32_t now_ms); /* RC ch5 edge; 1 = handled as WFB_PRIM_CMD_LAND, 0 = caller runs today's code */
void    wfb_glue_disarmed(void);           /* disarm edge: clears flight, trip and takeover latches */

#endif /* WFB_GLUE_H */
