#include <string.h>
#include <math.h>
#include "esp_log.h"
#include "esp_timer.h"
#include "closed_loop.h"

static const char *TAG = "closed_loop";

/* Internal state */
static struct {
    float    last_current_ma;
    float    session_current_ma;
    int64_t  session_start_us;
    int64_t  session_active_us;
    int64_t  cooldown_start_us;
    int64_t  last_inference_us;
    int      session_active;      /* 1 if actively stimulating */
    int      in_cooldown;         /* 1 if in cooldown period */
    float    prev_focus_score;
    float    prev_relax_score;
} cl_state;

/* Helper: clamp value between min and max */
static inline float clampf(float val, float min, float max)
{
    if (val < min) return min;
    if (val > max) return max;
    return val;
}

void closed_loop_init(void)
{
    memset(&cl_state, 0, sizeof(cl_state));
    cl_state.last_current_ma    = 0.0f;
    cl_state.session_start_us   = esp_timer_get_time();
    cl_state.last_inference_us  = esp_timer_get_time();
    cl_state.session_active     = 0;
    cl_state.in_cooldown        = 0;
    cl_state.prev_focus_score   = 50.0f;
    cl_state.prev_relax_score   = 50.0f;

    ESP_LOGI(TAG, "Closed-loop engine initialized");
}

void closed_loop_process(const FocusOutput *focus, ClosedLoopOutput *out)
{
    if (!out)
        return;

    int64_t now_us = esp_timer_get_time();

    /* Clear output defaults */
    memset(out, 0, sizeof(ClosedLoopOutput));

    /* Safety: check for nRF5340 timeout */
    if (focus == NULL) {
        if ((now_us - cl_state.last_inference_us) > (CL_COMMS_TIMEOUT_SEC * 1000000LL)) {
            ESP_LOGW(TAG, "nRF5340 comms timeout > %ds, shutting down stimulation",
                     CL_COMMS_TIMEOUT_SEC);
            out->current_ma   = 0.0f;
            out->stim_mode    = STIM_MODE_IDLE;
            out->focus_score  = 0.0f;
            out->brain_state  = FOCUS_STATE_NOISE;
            cl_state.last_current_ma = 0.0f;
            cl_state.session_active = 0;
        }
        return;
    }

    cl_state.last_inference_us = now_us;

    /* Calculate relaxation score from state */
    float relaxation_score;
    switch (focus->state) {
        case FOCUS_STATE_RELAXED:
            relaxation_score = focus->confidence * 100.0f;
            break;
        case FOCUS_STATE_DROWSY:
            relaxation_score = focus->confidence * 60.0f;
            break;
        case FOCUS_STATE_CALM:
            relaxation_score = focus->confidence * 50.0f;
            break;
        default:
            relaxation_score = 100.0f - focus->score;
            if (relaxation_score < 0) relaxation_score = 0;
            break;
    }

    /* Apply hysteresis to prevent oscillation */
    float focus_score = focus->score;
    float relax_score = relaxation_score;

    if (fabsf(focus_score - cl_state.prev_focus_score) < CL_MIN_FOCUS_HYSTERESIS) {
        focus_score = cl_state.prev_focus_score;
    }
    if (fabsf(relax_score - cl_state.prev_relax_score) < CL_MIN_FOCUS_HYSTERESIS) {
        relax_score = cl_state.prev_relax_score;
    }

    cl_state.prev_focus_score = focus_score;
    cl_state.prev_relax_score = relax_score;

    /* Set brain state in output */
    out->brain_state   = (int)focus->state;
    out->focus_score   = focus_score;
    out->relaxation_score = relax_score;

    /* Check cooldown */
    if (cl_state.in_cooldown) {
        int64_t cooldown_elapsed = (now_us - cl_state.cooldown_start_us) / 1000000LL;
        if (cooldown_elapsed >= CL_COOLDOWN_TIME_SEC) {
            cl_state.in_cooldown = 0;
            cl_state.session_active = 0;
            cl_state.session_start_us = now_us;
            ESP_LOGI(TAG, "Cooldown complete, session reset");
        } else {
            out->current_ma = 0.0f;
            out->stim_mode  = STIM_MODE_IDLE;
            ESP_LOGD(TAG, "In cooldown: %lld/%ds",
                     cooldown_elapsed, CL_COOLDOWN_TIME_SEC);
            return;
        }
    }

    /* Check session time */
    if (cl_state.session_active) {
        int64_t session_elapsed = (now_us - cl_state.session_start_us) / 1000000LL;
        if (session_elapsed >= CL_SESSION_TIME_SEC) {
            ESP_LOGI(TAG, "Session time limit reached (%ds), entering cooldown",
                     CL_SESSION_TIME_SEC);
            cl_state.in_cooldown = 1;
            cl_state.cooldown_start_us = now_us;
            cl_state.session_active = 0;
            out->current_ma = 0.0f;
            out->stim_mode  = STIM_MODE_IDLE;
            return;
        }
    }

    /* --- Rule engine --- */
    float target_current = 0.0f;
    int   target_mode    = STIM_MODE_IDLE;

    /* Rule 1: focus_score > 70 → tDCS anodal with proportional current */
    if (focus_score > 70.0f) {
        target_current = CL_DEFAULT_CURRENT_LOW + (focus_score - 70.0f) * CL_CURRENT_SCALE;
        target_mode    = STIM_MODE_TDCS_ANODAL;
    }
    /* Rule 2: relaxation_score > 70 → tACS 10Hz at 1.0 mA */
    else if (relax_score > 70.0f) {
        target_current = CL_DEFAULT_CURRENT_MED;
        target_mode    = STIM_MODE_TACS_10HZ;
    }
    /* Rule 3: focus_score < 40 → tDCS anodal at low current */
    else if (focus_score < 40.0f) {
        target_current = CL_DEFAULT_CURRENT_LOW;
        target_mode    = STIM_MODE_TDCS_ANODAL;
    }
    /* Rule 4: else → idle */
    else {
        target_current = 0.0f;
        target_mode    = STIM_MODE_IDLE;
    }

    /* Safety: clamp to absolute max */
    target_current = clampf(target_current, 0.0f, CL_MAX_CURRENT_MA);

    /* Ramp limiting: max change = RAMP_RATE per second */
    float max_delta = CL_RAMP_RATE_MAS;
    float current_delta = target_current - cl_state.last_current_ma;
    if (current_delta > max_delta)
        current_delta = max_delta;
    else if (current_delta < -max_delta)
        current_delta = -max_delta;

    float applied_current = cl_state.last_current_ma + current_delta;
    applied_current = clampf(applied_current, 0.0f, CL_MAX_CURRENT_MA);

    /* Update session tracking */
    if (applied_current > 0.05f && !cl_state.session_active) {
        cl_state.session_active = 1;
        cl_state.session_start_us = now_us;
        ESP_LOGI(TAG, "Stimulation session started");
    }

    if (applied_current <= 0.05f) {
        target_mode = STIM_MODE_IDLE;
        applied_current = 0.0f;
    }

    /* Store output */
    cl_state.last_current_ma = applied_current;
    out->current_ma = applied_current;
    out->stim_mode  = target_mode;

    ESP_LOGD(TAG, "Rules: focus=%.1f relax=%.1f curr=%.2fmA mode=%d",
             focus_score, relax_score, applied_current, target_mode);
}

void closed_loop_reset_timer(void)
{
    cl_state.session_start_us   = esp_timer_get_time();
    cl_state.last_inference_us  = esp_timer_get_time();
    cl_state.session_active     = 0;
    cl_state.in_cooldown        = 0;
    cl_state.last_current_ma    = 0.0f;
    ESP_LOGI(TAG, "Session timer reset");
}

int closed_loop_safety_check(void)
{
    int64_t now_us = esp_timer_get_time();
    int64_t since_inference = (now_us - cl_state.last_inference_us) / 1000000LL;

    if (since_inference > CL_COMMS_TIMEOUT_SEC) {
        ESP_LOGW(TAG, "Safety: no inference for %llds (limit %ds)",
                 since_inference, CL_COMMS_TIMEOUT_SEC);
        return 1;
    }

    if (cl_state.session_active) {
        int64_t session_elapsed = (now_us - cl_state.session_start_us) / 1000000LL;
        if (session_elapsed > CL_SESSION_TIME_SEC + 60) {
            ESP_LOGE(TAG, "Safety: session overrun! %llds", session_elapsed);
            return 1;
        }
    }

    return 0;
}
