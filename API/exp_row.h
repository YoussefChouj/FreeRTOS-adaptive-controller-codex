#ifndef EXP_ROW_H
#define EXP_ROW_H

#include <stdint.h>
#include <stddef.h>

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

/* Header-only (like pid_fallback.h): the Keil project lists every compiled .c and this adds none. */
static inline uint8_t Exp_Step(uint8_t req, uint8_t *last, uint8_t active, uint8_t on_ground,
                        const ExpRow_t *rows, uint8_t n,
                        const ExpKnobs_t *k, int8_t *xyrm_out)
{
    const ExpRow_t *r;

    if (last == NULL) {
        return active;
    }
    if (req == *last) {
        return active;
    }
    if (!on_ground) {
        return active;
    }
    if (req == 0xFFU) {
        *last = req;
        return 0xFFU;
    }
    if (rows == NULL || req >= n) {
        *last = req;
        return 0xEEU;
    }

    r = &rows[req];
    if (k != NULL) {
        if (r->xy != 0xFFU && k->xy_kind != NULL) {
            *k->xy_kind = r->xy;
        }
        if (r->kp != 0xFFU && k->kp_id != NULL) {
            *k->kp_id = r->kp;
        }
        if (r->vp != 0xFFU && k->vp_id != NULL) {
            *k->vp_id = r->vp;
        }
        if (r->lim != 0xFFU && k->lim_mode != NULL) {
            *k->lim_mode = r->lim;
        }
        if (r->mdec != 0xFFU && k->mdec != NULL) {
            *k->mdec = r->mdec;
        }
    }
    if (xyrm_out != NULL) {
        if (r->xyrm >= 0) {
            *xyrm_out = r->xyrm;
        } else if (k != NULL && k->xy_kind != NULL) {
            *xyrm_out = (int8_t)*k->xy_kind;
        } else {
            *xyrm_out = (r->xy != 0xFFU) ? (int8_t)r->xy : 0;
        }
    }
    *last = req;
    return req;
}

#ifdef __cplusplus
}
#endif

#endif /* EXP_ROW_H */
