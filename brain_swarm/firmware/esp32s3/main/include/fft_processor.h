#ifndef FFT_PROCESSOR_H
#define FFT_PROCESSOR_H

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/* Number of EEG channels */
#define FFT_NUM_CHANNELS    4

/* Sample rate and FFT parameters */
#define FFT_SAMPLE_RATE     250
#define FFT_SAMPLES_PER_SEC 256
#define FFT_SIZE            512

/* Number of frequency bands */
#define FFT_NUM_BANDS       5

/* Frequency band indices */
#define FFT_BAND_DELTA      0   /*  0.5 -  4 Hz */
#define FFT_BAND_THETA      1   /*  4   -  8 Hz */
#define FFT_BAND_ALPHA      2   /*  8   - 13 Hz */
#define FFT_BAND_BETA       3   /* 13   - 30 Hz */
#define FFT_BAND_GAMMA      4   /* 30   - 45 Hz */

/* Frequency bins per band (resolution ~0.488 Hz/bin) */
/* Bin range definitions */
#define FFT_BIN_START_DELTA     1
#define FFT_BIN_END_DELTA       8
#define FFT_BIN_START_THETA     9
#define FFT_BIN_END_THETA       16
#define FFT_BIN_START_ALPHA     17
#define FFT_BIN_END_ALPHA       26
#define FFT_BIN_START_BETA      27
#define FFT_BIN_END_BETA        61
#define FFT_BIN_START_GAMMA     62
#define FFT_BIN_END_GAMMA       92

/* FFT processor API */
void fft_init(void);
void fft_process(const int32_t samples[FFT_NUM_CHANNELS][FFT_SAMPLES_PER_SEC],
                 float band_powers[FFT_NUM_CHANNELS][FFT_NUM_BANDS]);

/* Utility */
const char* fft_band_name(int band);

#ifdef __cplusplus
}
#endif

#endif /* FFT_PROCESSOR_H */
