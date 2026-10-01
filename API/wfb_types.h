#ifndef WFB_TYPES_H
#define WFB_TYPES_H

/* Workflow B shared types. Contract: docs/workflow-b/interfaces.md section 3. */

#include <stdint.h>

typedef enum {
    WFB_ERR_NONE = 0,
    WFB_ERR_STATE,
    WFB_ERR_RANGE,
    WFB_ERR_COUNT,
    WFB_ERR_CRC,
    WFB_ERR_TIME,
    WFB_ERR_BOUNDS,
    WFB_ERR_ENDPOINT,
    WFB_ERR_SPEED,
    WFB_ERR_SAFETY
} wfb_err_t;

/* One trajectory sample, 20 bytes. Metres, degrees, seconds. */
typedef struct {
    float x_m;
    float y_m;
    float z_m;
    float yaw_deg;
    float t_s;
} wfb_traj_point_t;

#endif /* WFB_TYPES_H */
