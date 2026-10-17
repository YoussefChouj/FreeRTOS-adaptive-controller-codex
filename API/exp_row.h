#ifndef EXP_ROW_H
#define EXP_ROW_H

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef struct {
    uint8_t xy;
    uint8_t kp;
    uint8_t vp;
    uint8_t lim;
    uint8_t mdec;
    int8_t  xyrm;
    uint8_t layer;
} ExpRow_t;

typedef struct {
    float ref_type;
    float bw;
    float crm_ell;
    float g;
} XyRm_t;

typedef struct {
    volatile uint8_t *xy_kind;
    volatile uint8_t *kp_id;
    volatile uint8_t *vp_id;
    volatile uint8_t *lim_mode;
    volatile uint8_t *mdec;
} ExpKnobs_t;

uint8_t Exp_Step(uint8_t req, uint8_t *last, uint8_t active, uint8_t on_ground,
                 const ExpRow_t *rows, uint8_t n,
                 const ExpKnobs_t *k, int8_t *xyrm_out);

#ifdef __cplusplus
}
#endif

#endif /* EXP_ROW_H */
