/**
  ******************************************************************************
  * @file    main.c
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
 
/* Includes ------------------------------------------------------------------*/
#include "main.h"

/* Private includes ----------------------------------------------------------*/
/* USER CODE BEGIN Includes */
#include <stdarg.h>
#include <assert.h>
#include <stdint.h>
#include <stdbool.h>
#include <stdio.h>
#include <string.h>
#include <stdlib.h>

#include "stm32c0xx_hal.h"

#include "iousart.h"
#include "pa.h"
#include "ADS1220.h"
/* USER CODE END Includes */

/* Private typedef -----------------------------------------------------------*/
/* USER CODE BEGIN PTD */

/* USER CODE END PTD */

/* Private define ------------------------------------------------------------*/
/* USER CODE BEGIN PD */

/* USER CODE END PD */

/* Private macro -------------------------------------------------------------*/
/* USER CODE BEGIN PM */

/* USER CODE END PM */

/* Private variables ---------------------------------------------------------*/

I2C_HandleTypeDef hi2c1;

TIM_HandleTypeDef htim1;
TIM_HandleTypeDef htim3;
TIM_HandleTypeDef htim14;

/* USER CODE BEGIN PV */

/* USER CODE END PV */

/* Private function prototypes -----------------------------------------------*/
void SystemClock_Config(void);
static void MX_GPIO_Init(void);
static void MX_TIM1_Init(void);
static void MX_TIM3_Init(void);
static void MX_I2C1_Init(void);
static void MX_TIM14_Init(void);
/* USER CODE BEGIN PFP */

/* USER CODE END PFP */

/* Private user code ---------------------------------------------------------*/
/* USER CODE BEGIN 0 */

#define USART_TXBUFF_SIZE   128
char usart_txBuff[USART_TXBUFF_SIZE];

#define PA_OSR      7  // CLOCK_OSR_16384
#define ENDSTOP_OSR  2 //  2:CLOCK_OSR_512      1:CLOCK_OSR_256
unsigned char status_clk_old=0;
typedef struct
{

    unsigned char version[16];// pandapi3d v1.0
    unsigned char measue_data[32]; //W:1.722;D:0310;Y:-005;R:0;
    unsigned char status_clk; // endstop mode(e;)  pa mode(l;)
    unsigned char out_data_mode; // out raw data(d;), disable raw data output (D;)
    unsigned char THRHOLD_Z; // xx; E.g  12;
    unsigned char range;
    unsigned char set_normal; // 0:auto find normal_z ; 1:use current data as the normal_z ; 2: disable auto find normal_z
    unsigned char invert_data;
    // other variable

} Receive_D;

Receive_D  R_CMD;

//#define RAW_DATE_LEN 256*3

//unsigned char THRHOLD_Z=9;


// int out_data_mode = 0;

extern int r_index;
extern int normal;

int raw_dat[RAW_DATE_LEN];
int r_index=0;
int edge,read_pa=-1;
unsigned char pa_result[128];
unsigned char pa_list=0;

uint8_t rxData[64],tmp_r;
int re_index=0,re_end=0;
int normal_tmp[5]={0,0,0,0,0},normal_z=0,z_cnt=0;
unsigned int tim14_n =0 ;
int end_z=0;
void find_normal_endstop(unsigned int *r_data,int length,char force);
void USART2_printf(char *fmt,...);
void set_open(int log);

unsigned char process_cmd(void)
{
    int j=0;
    unsigned char cmd=0;
    if(sizeof(rxData)<1)
        return 1;
    for(j=1;j<sizeof(rxData);j++)
    {
        if(rxData[j]==';')
        {
            cmd = rxData[j-1];
            if(cmd=='l'){ // in pressure advance mode
                USART2_printf("PA mode\n");
                R_CMD.status_clk= PA_OSR;
            }
            else if(cmd=='e'){ // in probe mode
                USART2_printf("endstop mode\n");
                R_CMD.status_clk= ENDSTOP_OSR;
                find_normal_endstop(raw_dat,r_index,1);
            }
            else if(cmd=='d'){ // out data
                R_CMD.out_data_mode=1;
            }
            else if(cmd=='D'){ // disable data out
                R_CMD.out_data_mode=0;
            }
            else if(cmd=='i'){ // inverat the raw data
                R_CMD.invert_data=0;
            }
            else if(cmd=='I'){ //
                R_CMD.invert_data=1;
            }
            else if(cmd=='N'){ // use the current data
                R_CMD.set_normal=1;
                set_open(0);
            }
            else if(cmd=='n'){ // auto find normal_z by default
                R_CMD.set_normal=0;
            }
            else if((cmd>='0')&&(cmd<='9')){ // disable data out
                R_CMD.THRHOLD_Z=cmd-'0';
                if(j>=2){
                    if(rxData[j-2]>'0'&&rxData[j-2]<='9'){
                        R_CMD.THRHOLD_Z+=(rxData[j-2]-'0')*10;
                    }
                }
                USART2_printf("THRHOLD_Z: %d\n",R_CMD.THRHOLD_Z);
            }
            memset(rxData,0,sizeof(rxData));
            break;
        }

    }
    return cmd;
}

void USART2_printf(char *fmt,...)
{
    uint32_t i,length;
    va_list ap;
    va_start(ap,fmt);
    vsprintf(usart_txBuff,fmt,ap);
    va_end(ap);
    length=strlen((const char*)usart_txBuff);
    while((USART2->ISR&0x40)==0);
    for(i=0;i<length;i++)
    {
        iouart1_SendByte(usart_txBuff[i]);
    }
}
/*
void HAL_TIM_PeriodElapsedCallback(TIM_HandleTypeDef *htim) // 2ms
{
    if (htim->Instance == htim14.Instance)
    {
        tim14_n++;
    }
}
*/
uint8_t *ram_i2c;
uint8_t offset;
static uint8_t first_byte_state = 1;

void HAL_I2C_ListenCpltCallback(I2C_HandleTypeDef *hi2c)
{
    if (hi2c->Instance==I2C1){
        first_byte_state = 1;
        offset = 0;
        HAL_I2C_EnableListen_IT(hi2c);
    }
}

void HAL_I2C_AddrCallback(I2C_HandleTypeDef *hi2c, uint8_t TransferDirection, uint16_t AddrMatchCode)
{
    if (hi2c->Instance==I2C1){

        if(TransferDirection == I2C_DIRECTION_TRANSMIT)
        {
            if(first_byte_state)
            {
                HAL_I2C_Slave_Seq_Receive_IT(hi2c, &offset, 1, I2C_NEXT_FRAME);
            }
        }
        else
        {
            HAL_I2C_Slave_Seq_Transmit_IT(hi2c, &ram_i2c[offset], 1, I2C_NEXT_FRAME);
        }
    }
}

void HAL_I2C_SlaveRxCpltCallback(I2C_HandleTypeDef *hi2c)
{
    if (hi2c->Instance==I2C1){
        if(first_byte_state)
        {
            first_byte_state = 0;
        }
        else
        {
            offset++;
        }
        HAL_I2C_Slave_Seq_Receive_IT(hi2c, &ram_i2c[offset], 1, I2C_NEXT_FRAME);
    }
}

void HAL_I2C_SlaveTxCpltCallback(I2C_HandleTypeDef *hi2c)
{
    if (hi2c->Instance==I2C1){
        offset++;
        HAL_I2C_Slave_Seq_Transmit_IT(hi2c, &ram_i2c[offset], sizeof(Receive_D), I2C_NEXT_FRAME);
    }
}


/* USER CODE END PFP */

/* Private user code ---------------------------------------------------------*/
/* USER CODE BEGIN 0 */
/*
// ????????
void HAL_UART_RxCpltCallback(UART_HandleTypeDef *huart) {
    if (huart->Instance == USART2) {
        if( HAL_UART_Receive_IT(&huart2, &tmp_r, 1)==HAL_OK ){
                    rxData[re_index++]=tmp_r;
                    if(re_index>=sizeof(rxData))
                        re_index = 0;

             }

                //rxData[re_index]=0;
    }
}
*/

int get_ix(int i){
    if (i<0)
        i = RAW_DATE_LEN + i;
    return i;
}


void set_trigered(int log)
{
    int i=0;
    end_z=1;
    HAL_GPIO_WritePin(GPIOA,GPIO_PIN_8,GPIO_PIN_SET);
    HAL_GPIO_WritePin(GPIOA,GPIO_PIN_13,GPIO_PIN_SET);
    if(log==0)
        return;
    USART2_printf("n%d,%d\n",normal_z,R_CMD.THRHOLD_Z);
    for(i=r_index-1;i>r_index-10;i--)
        USART2_printf("%d,",raw_dat[get_ix(i)]);

}

void set_open(int log)
{
    end_z=0;
    HAL_GPIO_WritePin(GPIOA,GPIO_PIN_8,GPIO_PIN_RESET);
    HAL_GPIO_WritePin(GPIOA,GPIO_PIN_13,GPIO_PIN_RESET);
    if(log==0)
        return;
    USART2_printf("O%d\n",raw_dat[r_index-1]);

}

///////////////////////////

void find_normal_endstop(unsigned int *r_data,int length,char force)
{

    int s_avt = 0,i,min=0,max=0;
    /*  if (normal_z==0&&length>=4)
        {
            normal_z = r_data[length-1]+r_data[length-2]+r_data[length-3]+r_data[length-4];
            normal_z = normal_z/4;
            USART2_printf("n:%d\n",s_avt);
            return;
        }*/
    if(length<=31)
        return;


    min=r_data[get_ix(length-1)];
    max=r_data[get_ix(length-1)];
    for(i=length-1;i>length-1-30;i--)
    {
        if(min>r_data[get_ix(i)])
            min=r_data[get_ix(i)];
        if(max<r_data[get_ix(i)])
            max=r_data[get_ix(i)];
    }
    R_CMD.range=max-min;

    if((R_CMD.range>R_CMD.THRHOLD_Z || R_CMD.range>20)&&force==0)
        return;
    for(i=length-1;i>length-1-30;i--)
        s_avt = s_avt+r_data[get_ix(i)];
    s_avt = s_avt/30;
    if(normal_z!= s_avt)
        USART2_printf("N:%d\n",s_avt);
    normal_z= s_avt;
    tim14_n=0;

}
#define AVT_CN 4
int process_triggered(void)
{
    int s_avt = 0,i,tmp_cn=0,vibration;
    if (r_index>=RAW_DATE_LEN){
        r_index=0;
    }
    tmp_cn=2500;

    if((tim14_n%tmp_cn)==(tmp_cn-1))

    {
        tim14_n=0;
        find_normal_endstop(raw_dat,r_index,0);
    }

    for(i=r_index-1;i>r_index-1-AVT_CN;i--)
        s_avt = s_avt+raw_dat[get_ix(i)];
    s_avt = s_avt/AVT_CN;

    vibration =1;
    if(abs(s_avt-normal_z)>=R_CMD.THRHOLD_Z
            &&end_z==0 ){
        set_trigered(1);
        return 1;
    }
    else if(abs(s_avt-normal_z)<=(R_CMD.THRHOLD_Z/2)&&end_z==1){
        set_open(1);
        return 0;
    }
}


void Pressure_advance(void)
{
    if (r_index>(3*SAMPLES+1)){
        if (r_index >= RAW_DATE_LEN){
            r_index = 0;
            for(r_index=0;r_index<2*SAMPLES;r_index++)
                raw_dat[r_index] = raw_dat[RAW_DATE_LEN-2*SAMPLES-1+r_index];
            r_index = 2*SAMPLES;
            return;
        }
        find_normal(raw_dat,r_index);
        edge =has_plus(raw_dat,r_index-1*SAMPLES-5);
        if (edge > 2*SAMPLES){
            if (edge > 0){
                int ret = get_low_value(raw_dat,edge);
                if(ret>0){
                    pa_result[pa_list] = ret;
                    pa_list++;
                    r_index = 0;
                    memset(raw_dat,0,sizeof(raw_dat));
                }
            }
        }
    }

}

void Flash_OB_Handle(void) {
    FLASH_OBProgramInitTypeDef optionsbytesstruct;
    bool UPDATE = false;

    HAL_FLASHEx_OBGetConfig(&optionsbytesstruct);
    uint32_t userconfig = optionsbytesstruct.USERConfig;

    if((userconfig & FLASH_OPTR_nBOOT_SEL_Msk) != OB_BOOT0_FROM_PIN) {
        userconfig &= ~FLASH_OPTR_nBOOT_SEL_Msk;
        userconfig |= OB_BOOT0_FROM_PIN;
        UPDATE = true;
    }

    if(UPDATE) {
        optionsbytesstruct.USERConfig = userconfig;
        HAL_FLASH_Unlock();
        HAL_FLASH_OB_Unlock();
        HAL_FLASHEx_OBProgram(&optionsbytesstruct);
        HAL_FLASH_OB_Launch();
        HAL_FLASH_OB_Lock();
        HAL_FLASH_Lock();
    }
}
/* USER CODE END 0 */

/**
  * @brief  The application entry point.
  * @retval int
  */
int main(void)
{
    /* USER CODE BEGIN 1 */
    unsigned int cn_t=0,i=0;
    unsigned char cmd=0;
    int adctmp;
    int tempA;

    /* USER CODE END 1 */

    /* MCU Configuration--------------------------------------------------------*/

    /* Reset of all peripherals, Initializes the Flash interface and the Systick. */
    HAL_Init();

    /* USER CODE BEGIN Init */

    /* USER CODE END Init */

    /* Configure the system clock */
    SystemClock_Config();

    /* USER CODE BEGIN SysInit */

    /* USER CODE END SysInit */

    /* Initialize all configured peripherals */
    MX_GPIO_Init();
    MX_TIM1_Init();
    MX_TIM3_Init();
    MX_I2C1_Init();
    MX_TIM14_Init();
    /* USER CODE BEGIN 2 */
    HAL_TIM_Base_Start_IT(&htim1);
    HAL_TIM_Base_Start_IT(&htim14);
    re_index=0;
    re_end=0;
    offset=0;
    Flash_OB_Handle();
    memset(&R_CMD.version[0],0,sizeof(R_CMD));

    ram_i2c = &R_CMD.version[0];
    sprintf(R_CMD.version,"pandapi3dv1\n");
    R_CMD.THRHOLD_Z=4;
    R_CMD.status_clk=ENDSTOP_OSR;

    normal_z = 0;
    end_z = 0;
    if(HAL_I2C_EnableListen_IT(&hi2c1) != HAL_OK)
    {
        Error_Handler();
    }
    ADS1220_Init(4,0xD4);
    R_CMD.invert_data=1;
    /* USER CODE END 2 */

    /* Infinite loop */
    /* USER CODE BEGIN WHILE */
    while (1)
    {

        tempA = GetAD(4,1);
        if(PolarFlag==R_CMD.invert_data)
            tempA=-tempA;
        tempA=tempA/1000   + 6000;

        raw_dat[r_index++]=tempA;
        if(R_CMD.out_data_mode==1)
        {
            USART2_printf("%d;\n",tempA);
        }
        if(R_CMD.status_clk==PA_OSR)
            Pressure_advance();
        else
            process_triggered();
        process_cmd();
        if(R_CMD.set_normal==1||normal_z==0)
        {
            R_CMD.set_normal=2;
            find_normal_endstop(raw_dat,r_index,1);
            set_open(0);
        }
        if(status_clk_old!=R_CMD.status_clk)
        {
            status_clk_old=R_CMD.status_clk;
            if(R_CMD.status_clk==PA_OSR){
                ADS1220_Init(4,0x34);
                USART2_printf("PA mode\n");
            }
            else{
                ADS1220_Init(4,0xB4);
                USART2_printf("Endstop mode\n");
            }
        }


        /* USER CODE END WHILE */

        /* USER CODE BEGIN 3 */
    }
    /* USER CODE END 3 */
}

/**
  * @brief System Clock Configuration
  * @retval None
  */
void SystemClock_Config(void)
{
    RCC_OscInitTypeDef RCC_OscInitStruct = {0};
    RCC_ClkInitTypeDef RCC_ClkInitStruct = {0};

    RCC_OscInitStruct.OscillatorType = RCC_OSCILLATORTYPE_HSI;
    RCC_OscInitStruct.HSIState = RCC_HSI_ON;
    RCC_OscInitStruct.HSIDiv = RCC_HSI_DIV1;
    RCC_OscInitStruct.HSICalibrationValue = RCC_HSICALIBRATION_DEFAULT;
    if (HAL_RCC_OscConfig(&RCC_OscInitStruct) != HAL_OK)
    {
        Error_Handler();
    }

    RCC_ClkInitStruct.ClockType = RCC_CLOCKTYPE_HCLK|RCC_CLOCKTYPE_SYSCLK
                                |RCC_CLOCKTYPE_PCLK1;
    RCC_ClkInitStruct.SYSCLKSource = RCC_SYSCLKSOURCE_HSI;
    RCC_ClkInitStruct.SYSCLKDivider = RCC_SYSCLK_DIV1;
    RCC_ClkInitStruct.AHBCLKDivider = RCC_HCLK_DIV1;
    RCC_ClkInitStruct.APB1CLKDivider = RCC_APB1_DIV1;

    if (HAL_RCC_ClockConfig(&RCC_ClkInitStruct, FLASH_LATENCY_1) != HAL_OK)
    {
        Error_Handler();
    }
}

/**
  * @brief I2C1 Initialization Function
  * @param None
  * @retval None
  */
static void MX_I2C1_Init(void)
{

    /* USER CODE BEGIN I2C1_Init 0 */

    /* USER CODE END I2C1_Init 0 */

    /* USER CODE BEGIN I2C1_Init 1 */

    /* USER CODE END I2C1_Init 1 */
    hi2c1.Instance = I2C1;
    hi2c1.Init.Timing = 0x20303E5D;
    hi2c1.Init.OwnAddress1 = 8;
    hi2c1.Init.AddressingMode = I2C_ADDRESSINGMODE_7BIT;
    hi2c1.Init.DualAddressMode = I2C_DUALADDRESS_DISABLE;
    hi2c1.Init.OwnAddress2 = 0;
    hi2c1.Init.OwnAddress2Masks = I2C_OA2_NOMASK;
    hi2c1.Init.GeneralCallMode = I2C_GENERALCALL_DISABLE;
    hi2c1.Init.NoStretchMode = I2C_NOSTRETCH_DISABLE;
    if (HAL_I2C_Init(&hi2c1) != HAL_OK)
    {
        Error_Handler();
    }

    /** Configure Analogue filter
    */
    if (HAL_I2CEx_ConfigAnalogFilter(&hi2c1, I2C_ANALOGFILTER_ENABLE) != HAL_OK)
    {
        Error_Handler();
    }

    /** Configure Digital filter
    */
    if (HAL_I2CEx_ConfigDigitalFilter(&hi2c1, 0) != HAL_OK)
    {
        Error_Handler();
    }
    /* USER CODE BEGIN I2C1_Init 2 */

    /* USER CODE END I2C1_Init 2 */

}

/**
  * @brief TIM1 Initialization Function
  * @param None
  * @retval None
  */
static void MX_TIM1_Init(void)
{

    /* USER CODE BEGIN TIM1_Init 0 */

    /* USER CODE END TIM1_Init 0 */

    TIM_ClockConfigTypeDef sClockSourceConfig = {0};
    TIM_MasterConfigTypeDef sMasterConfig = {0};

    /* USER CODE BEGIN TIM1_Init 1 */

    /* USER CODE END TIM1_Init 1 */
    htim1.Instance = TIM1;
    htim1.Init.Prescaler = 48-1;
    htim1.Init.CounterMode = TIM_COUNTERMODE_UP;
    htim1.Init.Period = 65535-1;
    htim1.Init.ClockDivision = TIM_CLOCKDIVISION_DIV1;
    htim1.Init.RepetitionCounter = 0;
    htim1.Init.AutoReloadPreload = TIM_AUTORELOAD_PRELOAD_DISABLE;
    if (HAL_TIM_Base_Init(&htim1) != HAL_OK)
    {
        Error_Handler();
    }
    sClockSourceConfig.ClockSource = TIM_CLOCKSOURCE_INTERNAL;
    if (HAL_TIM_ConfigClockSource(&htim1, &sClockSourceConfig) != HAL_OK)
    {
        Error_Handler();
    }
    sMasterConfig.MasterOutputTrigger = TIM_TRGO_RESET;
    sMasterConfig.MasterOutputTrigger2 = TIM_TRGO2_RESET;
    sMasterConfig.MasterSlaveMode = TIM_MASTERSLAVEMODE_DISABLE;
    if (HAL_TIMEx_MasterConfigSynchronization(&htim1, &sMasterConfig) != HAL_OK)
    {
        Error_Handler();
    }
    /* USER CODE BEGIN TIM1_Init 2 */

    /* USER CODE END TIM1_Init 2 */

}

/**
  * @brief TIM3 Initialization Function
  * @param None
  * @retval None
  */
static void MX_TIM3_Init(void)
{

    /* USER CODE BEGIN TIM3_Init 0 */

    /* USER CODE END TIM3_Init 0 */

    TIM_ClockConfigTypeDef sClockSourceConfig = {0};
    TIM_MasterConfigTypeDef sMasterConfig = {0};

    /* USER CODE BEGIN TIM3_Init 1 */

    /* USER CODE END TIM3_Init 1 */
    htim3.Instance = TIM3;
    htim3.Init.Prescaler = 48-1;
    htim3.Init.CounterMode = TIM_COUNTERMODE_UP;
    htim3.Init.Period = IO_USART_SENDDELAY_TIME;
    htim3.Init.ClockDivision = TIM_CLOCKDIVISION_DIV1;
    htim3.Init.AutoReloadPreload = TIM_AUTORELOAD_PRELOAD_DISABLE;
    if (HAL_TIM_Base_Init(&htim3) != HAL_OK)
    {
        Error_Handler();
    }
    sClockSourceConfig.ClockSource = TIM_CLOCKSOURCE_INTERNAL;
    if (HAL_TIM_ConfigClockSource(&htim3, &sClockSourceConfig) != HAL_OK)
    {
        Error_Handler();
    }
    sMasterConfig.MasterOutputTrigger = TIM_TRGO_RESET;
    sMasterConfig.MasterSlaveMode = TIM_MASTERSLAVEMODE_DISABLE;
    if (HAL_TIMEx_MasterConfigSynchronization(&htim3, &sMasterConfig) != HAL_OK)
    {
        Error_Handler();
    }
    /* USER CODE BEGIN TIM3_Init 2 */

    /* USER CODE END TIM3_Init 2 */

}

/**
  * @brief TIM14 Initialization Function
  * @param None
  * @retval None
  */
static void MX_TIM14_Init(void)
{

    /* USER CODE BEGIN TIM14_Init 0 */

    /* USER CODE END TIM14_Init 0 */

    /* USER CODE BEGIN TIM14_Init 1 */

    /* USER CODE END TIM14_Init 1 */
    htim14.Instance = TIM14;
    htim14.Init.Prescaler = 1;
    htim14.Init.CounterMode = TIM_COUNTERMODE_UP;
    htim14.Init.Period = 48000;
    htim14.Init.ClockDivision = TIM_CLOCKDIVISION_DIV1;
    htim14.Init.AutoReloadPreload = TIM_AUTORELOAD_PRELOAD_DISABLE;
    if (HAL_TIM_Base_Init(&htim14) != HAL_OK)
    {
        Error_Handler();
    }
    /* USER CODE BEGIN TIM14_Init 2 */

    /* USER CODE END TIM14_Init 2 */

}

/**
  * @brief GPIO Initialization Function
  * @param None
  * @retval None
  */
static void MX_GPIO_Init(void)
{
    GPIO_InitTypeDef GPIO_InitStruct = {0};
    /* USER CODE BEGIN MX_GPIO_Init_1 */
    /* USER CODE END MX_GPIO_Init_1 */

    /* GPIO Ports Clock Enable */
    __HAL_RCC_GPIOA_CLK_ENABLE();
    __HAL_RCC_GPIOB_CLK_ENABLE();

    /*Configure GPIO pin Output Level */
    HAL_GPIO_WritePin(GPIOA, GPIO_PIN_2|GPIO_PIN_4|GPIO_PIN_5|GPIO_PIN_7
                            |GPIO_PIN_8|GPIO_PIN_11|GPIO_PIN_13, GPIO_PIN_RESET);

    /*Configure GPIO pins : PA2 PA4 PA5 PA7
                           PA8 PA11 PA13 */
    GPIO_InitStruct.Pin = GPIO_PIN_2|GPIO_PIN_4|GPIO_PIN_5|GPIO_PIN_7
                            |GPIO_PIN_8|GPIO_PIN_11|GPIO_PIN_13;
    GPIO_InitStruct.Mode = GPIO_MODE_OUTPUT_PP;
    GPIO_InitStruct.Pull = GPIO_PULLUP;
    GPIO_InitStruct.Speed = GPIO_SPEED_FREQ_LOW;
    HAL_GPIO_Init(GPIOA, &GPIO_InitStruct);

    /*Configure GPIO pins : PA3 PA6 */
    GPIO_InitStruct.Pin = GPIO_PIN_3|GPIO_PIN_6;
    GPIO_InitStruct.Mode = GPIO_MODE_INPUT;
    GPIO_InitStruct.Pull = GPIO_NOPULL;
    HAL_GPIO_Init(GPIOA, &GPIO_InitStruct);

    /*Configure GPIO pin : RX_Pin */
    GPIO_InitStruct.Pin = RX_Pin;
    GPIO_InitStruct.Mode = GPIO_MODE_IT_FALLING;
    GPIO_InitStruct.Pull = GPIO_PULLUP;
    HAL_GPIO_Init(RX_GPIO_Port, &GPIO_InitStruct);

    /* EXTI interrupt init*/
    HAL_NVIC_SetPriority(EXTI4_15_IRQn, 0, 0);
    HAL_NVIC_EnableIRQ(EXTI4_15_IRQn);

    /* USER CODE BEGIN MX_GPIO_Init_2 */
    /* USER CODE END MX_GPIO_Init_2 */
}

/* USER CODE BEGIN 4 */

/* USER CODE END 4 */

/**
  * @brief  This function is executed in case of error occurrence.
  * @retval None
  */
void Error_Handler(void)
{
    /* USER CODE BEGIN Error_Handler_Debug */
    /* User can add his own implementation to report the HAL error return state */
    __disable_irq();
    while (1)
    {
    }
    /* USER CODE END Error_Handler_Debug */
}

#ifdef  USE_FULL_ASSERT
/**
  * @brief  Reports the name of the source file and the source line number
  *         where the assert_param error has occurred.
  * @param  file: pointer to the source file name
  * @param  line: assert_param error line source number
  * @retval None
  */
void assert_failed(uint8_t *file, uint32_t line)
{
    /* USER CODE BEGIN 6 */
    /* User can add his own implementation to report the file name and line number,
     ex: printf("Wrong parameters value: file %s on line %d\r\n", file, line) */
    /* USER CODE END 6 */
}
#endif /* USE_FULL_ASSERT */