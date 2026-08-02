/*****************************************************************************
 * key.c — 独立按键驱动实现
 * 非阻塞状态机：Key_Tick() 每1ms由主循环节拍调用，
 * 电平稳定 20ms 才认定状态翻转（软件消抖），
 * 支持按下事件(边沿)与按住时长查询(用于长按重复/软降)。
 *****************************************************************************/

#include "key.h"

#define KEY_COUNT    4      /* 按键数量 */
#define DEBOUNCE_MS  20     /* 消抖时间 */
#define HELD_MAX     60000  /* 按住时长上限，防止溢出 */

/* 按键状态：0=松开 1=已确认按下 */
static unsigned char g_state[KEY_COUNT];
/* 上一次采样到的原始电平：0=低(按下) 1=高(松开) */
static unsigned char g_raw[KEY_COUNT];
/* 电平稳定计时 */
static unsigned char g_stableCnt[KEY_COUNT];
/* 已按住时长(ms) */
static unsigned int  g_heldMs[KEY_COUNT];
/* 按下事件标志(待取) */
static unsigned char g_evtFlag[KEY_COUNT];

/**
 * @brief  读取某键原始电平，0=按下(低电平) 1=松开(高电平)
 */
static unsigned char Key_ReadRaw(unsigned char idx)
{
    switch (idx)
    {
        case 0: return KEY_K1 ? 1 : 0;
        case 1: return KEY_K2 ? 1 : 0;
        case 2: return KEY_K3 ? 1 : 0;
        default: return KEY_K4 ? 1 : 0;
    }
}

void Key_Init(void)
{
    unsigned char i;
    KEY_K1 = 1;  /* 准双向口：写1后作输入 */
    KEY_K2 = 1;
    KEY_K3 = 1;
    KEY_K4 = 1;
    for (i = 0; i < KEY_COUNT; i++)
    {
        g_state[i] = 0;
        g_raw[i] = 1;
        g_stableCnt[i] = 0;
        g_heldMs[i] = 0;
        g_evtFlag[i] = 0;
    }
}

/**
 * @brief  节拍扫描，每1ms调用一次
 */
void Key_Tick(void)
{
    unsigned char i, raw;
    for (i = 0; i < KEY_COUNT; i++)
    {
        raw = Key_ReadRaw(i);
        if (raw != g_raw[i])
        {
            /* 电平发生变化，重新计时 */
            g_raw[i] = raw;
            g_stableCnt[i] = 0;
        }
        else if (g_stableCnt[i] < DEBOUNCE_MS)
        {
            g_stableCnt[i]++;
            if (g_stableCnt[i] == DEBOUNCE_MS)
            {
                /* 电平已稳定 20ms，确认状态翻转 */
                if (raw == 0 && g_state[i] == 0)    /* 确认按下 */
                {
                    g_state[i] = 1;
                    g_heldMs[i] = 0;
                    g_evtFlag[i] = 1;               /* 记录按下事件 */
                }
                else if (raw == 1 && g_state[i] == 1) /* 确认松开 */
                {
                    g_state[i] = 0;
                }
            }
        }
        if (g_state[i] == 1 && g_heldMs[i] < HELD_MAX)
        {
            g_heldMs[i]++;
        }
    }
}

/**
 * @brief  取走一个按键按下事件
 * @return KEY_LEFT/KEY_RIGHT/KEY_ROTATE/KEY_DROP，无事件返回 KEY_NONE
 */
unsigned char Key_GetEvent(void)
{
    unsigned char i;
    for (i = 0; i < KEY_COUNT; i++)
    {
        if (g_evtFlag[i])
        {
            g_evtFlag[i] = 0;
            return i + 1;   /* 数组下标0~3 对应 按键编号1~4 */
        }
    }
    return KEY_NONE;
}

/**
 * @brief  查询按键已持续按住的毫秒数
 * @param  key KEY_LEFT/KEY_RIGHT/KEY_ROTATE/KEY_DROP
 * @return 按住时长(ms)，未按住返回0
 */
unsigned int Key_HeldMs(unsigned char key)
{
    if (key >= KEY_LEFT && key <= KEY_DROP)
    {
        return g_heldMs[key - 1];
    }
    return 0;
}
