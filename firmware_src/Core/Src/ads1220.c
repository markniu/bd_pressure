
/**
  ******************************************************************************
  * @file    ads1220.c
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

#include "main.h"
#include "ADS1220.h"

unsigned char PolarFlag;
unsigned char Init_Config[4], channel0[8], channel1[8], channel2[8], channel3[8];

#define DIN_H HAL_GPIO_WritePin(GPIOA, GPIO_PIN_2, GPIO_PIN_SET)
#define DIN_L HAL_GPIO_WritePin(GPIOA, GPIO_PIN_2, GPIO_PIN_RESET)

#define SCK_H HAL_GPIO_WritePin(GPIOA, GPIO_PIN_5, GPIO_PIN_SET)
#define SCK_L HAL_GPIO_WritePin(GPIOA, GPIO_PIN_5, GPIO_PIN_RESET)

#define CS_H  HAL_GPIO_WritePin(GPIOA, GPIO_PIN_4, GPIO_PIN_SET)
#define CS_L  HAL_GPIO_WritePin(GPIOA, GPIO_PIN_4, GPIO_PIN_RESET)

#define ADS_CS_0  S_SPI_CSL
#define ADS_CS_1  S_SPI_CSH

void WriteOneByte(unsigned char command)
{
    unsigned char i;
    for (i = 0; i < 8; i++) {
        if (command & 0x80)
            DIN_H;
        else
            DIN_L;
        command <<= 1;
        SCK_H;
        SCK_L;
    }
}

char ReadOneByte(void)
{
    char result, i;
    SCK_L;
    for (i = 0; i < 8; i++) {
        SCK_H;
        result <<= 0x01;
        if (HAL_GPIO_ReadPin(GPIOA, GPIO_PIN_6))
            result |= 0x01;
        SCK_L;
    }
    return result;
}

long ReadData(void)
{
    long adval;
    char a, b, c;

    CS_L;
    WriteOneByte(RDATA);
    a = ReadOneByte();
    b = ReadOneByte();
    c = ReadOneByte();
    adval = a;
    adval = (adval << 8) | b;
    adval = (adval << 8) | c;
    CS_H;

    return adval;
}

void ADReset(void)
{
    CS_L;
    WriteOneByte(RESET);
    CS_H;
}

void ADPowerDown(void)
{
    CS_L;
    WriteOneByte(POWERDOWN);
    CS_H;
}

void ADStartConversion(void)
{
    CS_L;
    WriteOneByte(START);
    CS_H;
}

void ReadRegister(void)
{
    unsigned char i;
    unsigned long Data;
    CS_L;
    WriteOneByte(RREG | 0x03);
    for (i = 0; i < 4; i++) {
        Data += ReadOneByte();
    }
    CS_H;
    return;
}

void WriteRegister(unsigned char StartAddress, unsigned char NumRegs, unsigned char *pData)
{
    unsigned char i;
    CS_L;

    WriteOneByte(WREG | (((StartAddress << 2) & 0x0c) | ((NumRegs - 1) & 0x03)));

    for (i = 0; i < NumRegs; i++) {
        WriteOneByte(*pData);
        pData++;
    }
    CS_H;

    return;
}

void CofigAD(unsigned char channel, unsigned char speed)
{
    switch (channel) {
    case 0:
        Init_Config[0] = MUX_8 | PGA_0 | PGA_BYPASS_Disable;
        break;
    case 1:
        Init_Config[0] = MUX_9 | PGA_0 | PGA_BYPASS_Disable;
        break;
    case 2:
        Init_Config[0] = MUX_10 | PGA_0 | PGA_BYPASS_Disable;
        break;
    case 3:
        Init_Config[0] = MUX_11 | PGA_0 | PGA_BYPASS_Disable;
        break;
    case 4:
        Init_Config[0] = MUX_0 | PGA_12 | PGA_BYPASS_Enable;
        break;
    }

    Init_Config[1] = speed;
    Init_Config[2] = 0x10;
    Init_Config[3] = 0x00;

    WriteRegister(0x00, 0x04, Init_Config);
    ReadRegister();
}

void ADS1220_Init(unsigned char channel, unsigned char speed)
{
    CS_H;
    SCK_H;
    DIN_H;

    ADReset();
    CofigAD(channel, speed);
    ADStartConversion();
}

int GetAD(unsigned char channel, unsigned char continue_mode)
{
    int Result;

    while (HAL_GPIO_ReadPin(GPIOA, GPIO_PIN_3))
        ;
    Result = ReadData();

    if (continue_mode == 0) {
        ADPowerDown();
    }

    if (Result & 0x800000) {
        PolarFlag = 1;
        Result = ~Result;
        Result = Result & 0xffffff;
        Result = Result + 1;
    } else {
        PolarFlag = 0;
    }

    return Result;
}