/**
  ******************************************************************************
  * @file    pa.c
  * @author  Mark <markyueyue@gmail.com>
  * @version V1.0
  * @date    2026
  * @brief   Main program body – firmware entry point and core control loop.
  * @copyright Copyright (c) 2025 pandapi3d.com. All rights reserved.
  * @license This software is licensed under terms that can be found in the LICENSE file
  *          in the root directory of this software component.
  *          If no LICENSE file comes with this software, it is provided AS-IS.
  ******************************************************************************
  */
#include <stdarg.h>
#include <assert.h>
#include <stdint.h>
#include <stdbool.h>
#include <string.h>
#include <stdlib.h>
#include <stdio.h>

#include "pa.h"

int threshold = 2;
extern uint8_t *ram_i2c;

int normal_max = 0;
int normal_min = 0;
int normal = 0;
int low_count = 0;
int low_min = 0;
int low_max = 0;
int low = 0;

extern int raw_dat[RAW_DATE_LEN];
unsigned int low_data[128];
unsigned int out[10][2];
int H_left = 0;
int L_left = 0;
int H_right = 0;
int L_right = 0;

int H_left_value = 0;
int L_left_value = 0;
int H_right_value = 0;
int L_right_value = 0;

int k_left = 0;
int k_right = 0;
char resultp[100];
#define pa_step   0.002
extern int r_index;
int ii = 0, i = 0, k, j;

#define ABS_DIFF(a, b) ((a) > (b) ? (a) - (b) : (b) - (a))

void find_normal(unsigned int *r_data, int length)
{
    int s_avt = 0;
    for (i = length - 1; i > length - 1 - 20; i--)
        s_avt = s_avt + r_data[i];
    s_avt = s_avt / 20;

    for (i = length - 1; i > length - 1 - 20; i--)
        if (ABS_DIFF(r_data[i], s_avt) >= 2)
            return;

    if (abs(s_avt - normal) >= 2) {
        int min_t = normal - 2;
        for (i = length - 1; i > 0; i--) {
            if (r_data[i] < min_t)
                min_t = r_data[i];
        }
        normal = s_avt;
        threshold = (normal - min_t) / 2;
        if (threshold > 10)
            threshold = 10;
        else if (threshold < 2)
            threshold = 2;
    }
}

char has_plus(unsigned int *r_data, int length)
{
    if (threshold < 2)
        return 0;
    if ((r_data[length - 1] < (normal - threshold)) ||
        (r_data[length - 2] < (normal - threshold)) ||
        (length < SAMPLES / 2))
        return 0;

    for (i = length - 3; i > 10; i--) {
        if (r_data[i] <= (normal - threshold) &&
            r_data[i - 1] <= (normal - threshold) &&
            r_data[i - 2] <= (normal - threshold) &&
            r_data[i - 3] <= (normal - threshold) &&
            r_data[i - 4] <= (normal - threshold) &&
            r_data[i - 5] <= (normal - threshold))
            return i;
    }
    return 0;
}

int get_low_value(unsigned int *r_data, int length)
{
    unsigned short min_L = r_data[length - 1];
    low_count = 0;
    int min_pos = 0;
    memset(low_data, 0, sizeof(low_data));

    for (i = length - 1; i >= 0; i--) {
        if (r_data[i] <= (normal - threshold)) {
            low_data[low_count] = r_data[i];
            low_count = low_count + 1;
            if (low_count > 2 * SAMPLES)
                return 0;
            if (min_L > r_data[i]) {
                min_L = r_data[i];
                min_pos = i;
            }
        } else
            break;
    }

    if (low_count < 5 || min_L >= raw_dat[length - 1]) {
        low_count = 0;
        return 0;
    }

    int max_t = 0;
    L_left = 0;
    for (i = length - 1 - low_count; i < length; i++) {
        if ((raw_dat[i] - raw_dat[i + 1]) > (max_t - 1)) {
            max_t = raw_dat[i] - raw_dat[i + 1];
            L_left = i + 1;
        }
    }

    int av_t = 0;
    H_left = 0;
    for (i = L_left; i >= 0; i--) {
        av_t = (raw_dat[i - 1] + raw_dat[i - 2] + raw_dat[i - 3] +
                raw_dat[i - 4] + raw_dat[i - 5] + raw_dat[i - 6]) / 6;
        if (raw_dat[i] >= raw_dat[i - 1] && raw_dat[i] >= av_t) {
            H_left = i;
            break;
        }
    }

    H_right = length + 1 * SAMPLES;
    av_t = 0;
    for (i = length - 1; i < (length + 1 * SAMPLES - 6); i++) {
        av_t = (raw_dat[i + 1] + raw_dat[i + 2] + raw_dat[i + 3] +
                raw_dat[i + 4] + raw_dat[i + 5] + raw_dat[i + 6]) * 10 / 6;
        if (raw_dat[i] >= raw_dat[i + 1] && (raw_dat[i] * 10) >= av_t) {
            H_right = i;
            break;
        }
    }

    max_t = 0;
    L_right = L_left;
    for (i = length - 1; i > L_left; i--) {
        if ((raw_dat[i] - raw_dat[i - 1]) > (max_t - 1)) {
            max_t = raw_dat[i] - raw_dat[i - 1];
            L_right = i;
        }
        if (raw_dat[i] <= raw_dat[i - 1] && L_right > L_left)
            break;
    }

    char average_range = 5;
    H_left_value = 0;
    L_left_value = 0;
    L_right_value = 0;
    H_right_value = 0;

    for (i = H_left; i > H_left - average_range; i--) {
        H_left_value += raw_dat[i];
    }
    H_left_value = H_left_value * 10 / average_range;

    for (i = L_left; i < (L_left + average_range); i++) {
        L_left_value += raw_dat[i];
    }
    L_left_value = L_left_value * 10 / average_range;

    for (i = L_right; i > L_right - average_range; i--) {
        L_right_value += raw_dat[i];
    }
    L_right_value = L_right_value * 10 / average_range;

    for (i = H_right; i < (H_right + average_range); i++) {
        H_right_value += raw_dat[i];
    }
    H_right_value = H_right_value * 10 / average_range;

    k_right = H_right - L_right;
    k_left = L_left - H_left;
    int Hk = (raw_dat[H_right] + raw_dat[H_right + 1]) * 10 / 2;

    i = abs(Hk - av_t) / 10 + k_right +
        abs(H_right_value - H_left_value) / 10 + k_left;
    sprintf(ram_i2c + 16, "R:%d,%d,%d,%d,%d\n",
            i, k_left, k_right, (Hk - av_t), H_right_value - H_left_value);
    USART2_printf(ram_i2c + 16);

    if (i >= 225)
        i = 255;
    return i;
}