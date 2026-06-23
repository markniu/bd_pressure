/**
  ******************************************************************************
  * @file    iousart.c
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

#include <stdio.h>
#include "iousart.h"
#include "main.h"

#define u32 unsigned int
#define u16 unsigned short
#define u8  unsigned char

extern TIM_HandleTypeDef htim1;
extern TIM_HandleTypeDef htim3;
extern TIM_HandleTypeDef htim14;
extern unsigned int tim14_n;

u8 recvData = 0;
u8 recvStat = COM_STOP_BIT;

void iouart1_TXD(uint8_t option)
{
    if (1 == option) {
        HAL_GPIO_WritePin(GPIOA, GPIO_PIN_11, GPIO_PIN_SET);
    } else {
        HAL_GPIO_WritePin(GPIOA, GPIO_PIN_11, GPIO_PIN_RESET);
    }
}

uint8_t iouart1_RXD(void)
{
    return HAL_GPIO_ReadPin(GPIOA, GPIO_PIN_12);
}

void iouart1_delayUs(volatile u32 nTime)
{
    u16 tmp;

    tmp = __HAL_TIM_GET_COUNTER(&htim1);

    if (tmp + nTime <= 65535) {
        while ((__HAL_TIM_GET_COUNTER(&htim1) - tmp) < nTime);
    } else {
        __HAL_TIM_SET_COUNTER(&htim1, 0);
        while (__HAL_TIM_GET_COUNTER(&htim1) < nTime);
    }
}

void iouart1_SendByte(u8 datatoSend)
{
    u8 i, tmp;

    iouart1_TXD(0);
    iouart1_delayUs(IO_USART_SENDDELAY_TIME);

    for (i = 0; i < 8; i++) {
        tmp = (datatoSend >> i) & 0x01;

        if (tmp == 0) {
            iouart1_TXD(0);
            iouart1_delayUs(IO_USART_SENDDELAY_TIME);
        } else {
            iouart1_TXD(1);
            iouart1_delayUs(IO_USART_SENDDELAY_TIME);
        }
    }

    iouart1_TXD(1);
    iouart1_delayUs(IO_USART_SENDDELAY_TIME);
}

void HAL_GPIO_EXTI_Falling_Callback(uint16_t GPIO_Pin)
{
    if (GPIO_Pin == RX_Pin) {
        if (iouart1_RXD() == 0) {
            if (recvStat == COM_STOP_BIT) {
                recvStat = COM_START_BIT;
                HAL_TIM_Base_Start_IT(&htim3);
            }
        }
    }
}

extern uint8_t rxData[32];
extern int re_index;

void HAL_TIM_PeriodElapsedCallback(TIM_HandleTypeDef *htim)
{
    if (htim->Instance == htim3.Instance) {
        recvStat++;
        if (recvStat == COM_STOP_BIT) {
            HAL_TIM_Base_Stop_IT(&htim3);
            recvData = recvData;
            rxData[re_index++] = recvData;
            if (re_index >= sizeof(rxData))
                re_index = 0;
            return;
        }
        if (iouart1_RXD()) {
            recvData |= (1 << (recvStat - 1));
        } else {
            recvData &= ~(1 << (recvStat - 1));
        }
    }
    if (htim->Instance == htim14.Instance) {
        tim14_n++;
    }
}