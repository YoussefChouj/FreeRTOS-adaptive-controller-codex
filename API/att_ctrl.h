#ifndef ATT_CTRL_H
#define ATT_CTRL_H

#include <stdint.h>

#define ATT_CTRL_PID   0U
#define ATT_CTRL_ADRC  1U
#define ATT_CTRL_LQR   2U
#define ATT_CTRL_MPC   3U

#define ATT_MODE_OFF      0U
#define ATT_MODE_SHADOW   1U
#define ATT_MODE_ACTIVE   2U

#define ATT_ROLL  0U
#define ATT_PITCH 1U
#define ATT_YAW   2U
#define ATT_Z     3U

/* Same bounds as xy_ctrl.h, Z is asymmetric but handled separately */
#define ATT_CTRL_DMAX  48
#define ATT_CTRL_NMAX  10

/* Latch reason codes (g_att_latch) */
typedef enum {
    LATCH_NONE = 0,
    LATCH_NAN_INF,
    LATCH_ANGLE_BOUND,
    LATCH_RATE_BOUND,
    LATCH_YAW_ERR_BOUND,
    LATCH_U_SAT_TIMEOUT,
    LATCH_CPU_OVERRUN,
    LATCH_Z_NOT_VALID,
    LATCH_Z_TOO_LOW,
    LATCH_Z_LANDING,
    LATCH_Z_SINK_FAST,
    LATCH_Z_ERR_BOUND,
    LATCH_PID_FALLBACK
} AttCtrlLatchReason_e;

typedef struct {
    float m_est;
    float z;
    float z1;
    float z2;
    float u_prev;
    float a_eff;
    float rls_p[3];
    float plan[ATT_CTRL_NMAX];
    float pipe[ATT_CTRL_DMAX];
    uint8_t head;
    uint8_t init;
    uint8_t kind;
    uint8_t mode;
    uint8_t u_sat_ticks;
} AttCtrlState;

typedef struct {
    float r;
    float w_ff;
    float y;
    float w;
} AttCtrlIn;

extern volatile uint8_t g_att_mode[4];
extern volatile float g_att_alpha[4];
extern volatile uint8_t g_att_latch[4];
extern volatile uint8_t g_att_would_latch[4];
extern volatile uint8_t g_hov_ff_mode;
extern float g_att_v_filt;

void AttCtrl_TickBegin(void);
void AttCtrl_ResetAll(void);
float AttCtrl_Apply(uint8_t axis, float u_pid, const AttCtrlIn *in);

#endif /* ATT_CTRL_H */
