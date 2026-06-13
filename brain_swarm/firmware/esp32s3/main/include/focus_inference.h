#ifndef FOCUS_INFERENCE_H
#define FOCUS_INFERENCE_H

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/* Brain state enumeration (7 states + noise) */
typedef enum {
    FOCUS_STATE_DROWSY       = 0,
    FOCUS_STATE_RELAXED      = 1,
    FOCUS_STATE_CALM         = 2,
    FOCUS_STATE_ATTENTIVE    = 3,
    FOCUS_STATE_FOCUSED      = 4,
    FOCUS_STATE_DEEP_FOCUS   = 5,
    FOCUS_STATE_HYPERFOCUS   = 6,
    FOCUS_STATE_NOISE        = 7,
    FOCUS_STATE_COUNT        = 8
} focus_state_t;

/* Focus score mapping */
#define FOCUS_SCORE_DROWSY      15.0f
#define FOCUS_SCORE_RELAXED     40.0f
#define FOCUS_SCORE_CALM        50.0f
#define FOCUS_SCORE_ATTENTIVE   60.0f
#define FOCUS_SCORE_FOCUSED     75.0f
#define FOCUS_SCORE_DEEP_FOCUS  90.0f
#define FOCUS_SCORE_HYPERFOCUS  95.0f
#define FOCUS_SCORE_NOISE        0.0f

/* Focus output struct */
typedef struct {
    focus_state_t state;       /* Classified brain state */
    float         score;       /* Focus score 0-100 */
    float         confidence;  /* Softmax probability of winner */
} FocusOutput;

/* TFLite tensor arena size */
#define TENSOR_ARENA_SIZE   8192

/* Number of input features: 4 channels x 5 bands = 20 */
#define FOCUS_INPUT_SIZE    20

/* Number of output classes */
#define FOCUS_OUTPUT_SIZE   8

/* Focus inference API */
int  focus_init(void);
int  focus_run(const float band_powers[/*4*/][/*5*/], FocusOutput *out);
void focus_get_state_name(focus_state_t state, char *buf, size_t buf_size);

#ifdef __cplusplus
}
#endif

#endif /* FOCUS_INFERENCE_H */
