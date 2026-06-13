#ifndef CLOSED_LOOP_H
#define CLOSED_LOOP_H

#include <stdint.h>
#include "focus_inference.h"

#ifdef __cplusplus
extern "C" {
#endif

/* Stimulation modes */
#define STIM_MODE_IDLE          0
#define STIM_MODE_TDCS_ANODAL   1
#define STIM_MODE_TACS_10HZ     2
#define STIM_MODE_TACS_40HZ     3

/* Safety limits */
#define CL_MAX_CURRENT_MA       2.0f    /* Absolute max current */
#define CL_RAMP_RATE_MAS         0.5f    /* Max change per second */
#define CL_SESSION_TIME_SEC     2400    /* 40 minutes */
#define CL_COOLDOWN_TIME_SEC    1200    /* 20 minutes */
#define CL_COMMS_TIMEOUT_SEC    5       /* nRF5340 disconnect timeout */
#define CL_MIN_FOCUS_HYSTERESIS 5.0f    /* Hysteresis band for threshold crossing */
#define CL_DEFAULT_CURRENT_LOW  0.5f    /* Current when focus < 40 */
#define CL_DEFAULT_CURRENT_MED  1.0f    /* Current for relaxation */
#define CL_CURRENT_SCALE        0.025f  /* Per-point above 70 */

/* Closed-loop output */
typedef struct {
    float    focus_score;        /* 0-100 */
    float    relaxation_score;   /* 0-100 */
    int      brain_state;        /* focus_state_t */
    float    current_ma;         /* Output: requested tDCS current */
    int      stim_mode;          /* Output: stimulation mode */
} ClosedLoopOutput;

/* Closed-loop rule engine API */
void closed_loop_init(void);
void closed_loop_process(const FocusOutput *focus, ClosedLoopOutput *out);
void closed_loop_reset_timer(void);

/* Safety check - returns 1 if system should shut down */
int  closed_loop_safety_check(void);

#ifdef __cplusplus
}
#endif

#endif /* CLOSED_LOOP_H */
