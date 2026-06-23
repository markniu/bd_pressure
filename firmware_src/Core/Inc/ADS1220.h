#ifndef _ADS1220_H_
#define _ADS1220_H_

extern unsigned char PolarFlag;

#define RESET      0x03
#define START      0x08
#define POWERDOWN  0x02
#define RDATA      0x10
#define RREG       0x20
#define WREG       0x40

#define MUX_0      0x00
#define MUX_1      0x10
#define MUX_2      0x20
#define MUX_3      0x30
#define MUX_4      0x40
#define MUX_5      0x50
#define MUX_6      0x60
#define MUX_7      0x70
#define MUX_8      0x80
#define MUX_9      0x90
#define MUX_10     0xA0
#define MUX_11     0xB0
#define MUX_12     0xC0
#define MUX_13     0xD0
#define MUX_14     0xE0

#define PGA_0      0x00
#define PGA_1      0x02
#define PGA_4      0x04
#define PGA_8      0x06
#define PGA_16     0x08
#define PGA_32     0x0A
#define PGA_64     0x0C
#define PGA_12     0x0E

#define PGA_BYPASS_Enable   0x00
#define PGA_BYPASS_Disable  0x01

#define DR_20SPS    0x00
#define DR_45SPS    0x20
#define DR_90SPS    0x40
#define DR_175SPS   0x60
#define DR_330SPS   0x80
#define DR_600SPS   0xA0
#define DR_1000SPS  0xC0

#define MODE_0      0x00
#define MODE_1      0x08
#define MODE_2      0x10

#define ConverMode_0  0x00
#define ConverMode_1  0x04

#define TS_Disable  0x00
#define TS_Enable   0x02

#define BCS_Disable 0x00
#define BCS_Enable  0x01

#define VREF_0      0x00
#define VREF_1      0x40
#define VREF_2      0x80
#define VREF_3      0xC0

#define FIR_Mode0   0x00
#define FIR_Mode1   0x10
#define FIR_Mode2   0x20
#define FIR_Mode3   0x30

#define PSW_ON      0x00
#define PSW_OFF     0x08

#define IDAC_0      0x00
#define IDAC_1      0x01
#define IDAC_2      0x02
#define IDAC_3      0x03
#define IDAC_4      0x04
#define IDAC_5      0x05
#define IDAC_6      0x06
#define IDAC_7      0x07

#define IDAC1_0     0x00
#define IDAC1_1     0x20
#define IDAC1_2     0x40
#define IDAC1_3     0x60
#define IDAC1_4     0x80
#define IDAC1_5     0xA0
#define IDAC1_6     0xC0

#define IDAC2_0     0x00
#define IDAC2_1     0x04
#define IDAC2_2     0x08
#define IDAC2_3     0x0C
#define IDAC2_4     0x10
#define IDAC2_5     0x14
#define IDAC2_6     0x18

#define DRDY_Mode0  0x00
#define DRDY_Mode1  0x02

void ADS1220_Init(unsigned char channel, unsigned char speed);
int GetAD(unsigned char channel, unsigned char continue_mode);
void ADS1220_GPIOInit(void);

#endif