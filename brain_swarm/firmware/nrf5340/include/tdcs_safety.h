#ifndef NEURORESONATOR_TDCS_SAFETY_H
#define NEURORESONATOR_TDCS_SAFETY_H

#include <zephyr/types.h>
#include <stdbool.h>

#ifdef __cplusplus
extern "C" {
#endif

/* Safety limits */
#define MAX_CURRENT_MA          2.0f    /* Maximum stimulation current */
#define MAX_SESSION_MIN         40      /* Maximum session duration in minutes */
#define MAX_IMPEDANCE_KOHM      20      /* Maximum electrode impedance */
#define SESSION_WARN_MIN        35      /* Warning at 35 minutes */
#define RAMP_RATE               0.5f    /* Safe ramp rate mA/s */
#define IMPEDANCE_MEAS_CURRENT  0.0001f /* 100nA test current */
#define IMPEDANCE_MEAS_FREQ     100     /* 100Hz test frequency */
#define IMPEDANCE_MAX_VOLTAGE   2.0f    /* Max voltage for 100nA through 20kΩ */

/* Safety error states */
enum safety_error {
    SAFETY_OK = 0,
    ERR_HIGH_IMPEDANCE,
    ERR_OVERCURRENT,
    ERR_SESSION_TIMEOUT,
    ERR_ABORT
};

/* Safety state structure */
struct safety_state {
    enum safety_error last_error;
    float current_current_ma;
    float measured_impedance_kohm;
    uint32_t session_elapsed_sec;
    uint32_t session_start_ticks;
    bool stim_active;
    bool safe_stop_asserted;
    bool impedance_ok;
    bool overcurrent_ok;
    bool session_time_ok;
};

/* API */
int safety_init(void);
int safety_check(float current_ma, float impedance_kohm);
bool safety_ok(void);
enum safety_error safety_get_error(void);
int safety_tick(void);
int safety_start_session(void);
int safety_stop_session(void);
int safety_measure_impedance(float *impedance_kohm);
int safety_emergency_stop(void);
void safety_reset_error(void);
void safety_get_state(struct safety_state *state);

#ifdef __cplusplus
}
#endif

#endif /* NEURORESONATOR_TDCS_SAFETY_H */
