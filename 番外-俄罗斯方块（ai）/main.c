/*****************************************************************************
 * main.c — 主循环与游戏调度
 *
 * 架构：
 *   定时器0 产生 1ms 系统节拍（中断内只累加计数、置标志，不做业务）
 *   主循环每 1ms：按键扫描 → 分发按键事件 → 游戏逻辑步进
 *   每轮循环：增量渲染（仅在状态变化时实际写屏）
 *
 * 定时器初值 0xFC66 = 65536-922：11.0592MHz / 12T ≈ 921.6 计数/ms
 *****************************************************************************/

#include <REGX52.H>
#include <c51_isr.h>        /* INTERRUPT 宏：VSCode/Keil 双端兼容（位于 Keil INC 目录） */
#include "LCD1602.h"
#include "key.h"
#include "tetris.h"

volatile unsigned int  g_msTick = 0;    /* 系统毫秒节拍（中断累加） */
volatile unsigned char g_tickFlag = 0;  /* 1ms 节拍标志 */

/**
 * @brief 定时器0初始化：模式1(16位)，1ms中断 @11.0592MHz
 */
void Timer0_Init(void)
{
    TMOD &= 0xF0;
    TMOD |= 0x01;
    TH0 = 0xFC;
    TL0 = 0x66;
    ET0 = 1;
    EA  = 1;
    TR0 = 1;
}

/**
 * @brief 定时器0中断服务函数（中断号1）
 *        INTERRUPT(1) 在 Keil 端展开为 interrupt 1，VSCode 端展开为空
 */
void Timer0_ISR(void) INTERRUPT(1)
{
    TH0 = 0xFC;                 /* 重装 1ms 初值 */
    TL0 = 0x66;
    g_msTick++;
    g_tickFlag = 1;
}

void main()
{
    unsigned int now;
    unsigned char keyEvt;

    LCD1602_Init();
    Key_Init();
    Tetris_Init();
    Timer0_Init();

    while (1)
    {
        if (g_tickFlag)
        {
            g_tickFlag = 0;
            EA = 0;             /* 16位变量关中断原子读取，防止撕裂 */
            now = g_msTick;
            EA = 1;

            Key_Tick();                         /* 按键消抖扫描 */
            keyEvt = Key_GetEvent();
            if (keyEvt != KEY_NONE)
                Tetris_OnKey(keyEvt);           /* 分发按键事件 */
            Tetris_Update(now);                 /* 重力/长按重复/软降 */
        }
        Tetris_Render();                        /* 增量渲染（无变化立即返回） */
    }
}
