/*****************************************************************************
 * LCD1602.c — LCD1602 底层驱动实现
 * 时序沿用已验证的教程驱动（不读忙标志，固定延时）：
 *   命令/清屏用长延时，数据写用短延时(约100us)，
 *   保证全帧 CGRAM 刷新(72次写)控制在约8ms内，肉眼无感。
 * 晶振：11.0592MHz
 *****************************************************************************/

#include "LCD1602.h"

/* ---------- 长延时 约1ms（命令用） ---------- */
static void LCD1602_DelayMs(void)
{
    unsigned char i, j;
    i = 2;
    j = 199;
    do
    {
        while (--j);
    } while (--i);
}

/* ---------- 短延时 约100us（数据写用） ---------- */
static void LCD1602_DelayUs(void)
{
    unsigned char i;
    i = 46;
    while (--i);
}

/**
 * @brief  写命令
 * @note   普通命令执行时间约40us，用短延时即可；
 *         仅清屏(0x01)/归位(0x02)需1.52ms以上，用长延时。
 *         这样全帧CGRAM刷新可控制在约15ms内，肉眼无感。
 */
void LCD1602_WriteCmd(unsigned char cmd)
{
    LCD1602_RS = 0;
    LCD1602_RW = 0;
    LCD1602_DATA_PORT = cmd;
    LCD1602_EN = 1;
    LCD1602_DelayUs();
    LCD1602_EN = 0;
    if (cmd == 0x01 || cmd == 0x02)
    {
        LCD1602_DelayMs();
        LCD1602_DelayMs();
    }
    else
    {
        LCD1602_DelayUs();
    }
}

/**
 * @brief  写数据
 */
void LCD1602_WriteData(unsigned char dat)
{
    LCD1602_RS = 1;
    LCD1602_RW = 0;
    LCD1602_DATA_PORT = dat;
    LCD1602_EN = 1;
    LCD1602_DelayUs();
    LCD1602_EN = 0;
    LCD1602_DelayUs();
}

/**
 * @brief  定位光标
 * @param  col 列 0~15
 * @param  row 行 0~1
 */
void LCD1602_SetCursor(unsigned char col, unsigned char row)
{
    if (row == 0)
        LCD1602_WriteCmd(0x80 | col);
    else
        LCD1602_WriteCmd(0x80 | (0x40 + col));
}

/**
 * @brief  指定位置写一个字符
 * @param  ch 字符编码；0~7 对应 CGRAM 自定义字模
 */
void LCD1602_WriteChar(unsigned char col, unsigned char row, unsigned char ch)
{
    LCD1602_SetCursor(col, row);
    LCD1602_WriteData(ch);
}

/**
 * @brief  指定位置写字符串（自动截断到行尾由调用方保证长度）
 */
void LCD1602_WriteString(unsigned char col, unsigned char row, char *str)
{
    LCD1602_SetCursor(col, row);
    while (*str)
    {
        LCD1602_WriteData(*str);
        str++;
    }
}

/**
 * @brief  向 CGRAM 自定义字模槽写入 8 字节点阵
 * @param  slot    槽号 0~7
 * @param  pattern 指向8字节数组，每字节低5位=该行5个像素(bit4为最左像素)
 */
void LCD1602_LoadCGRAM(unsigned char slot, unsigned char *pattern)
{
    unsigned char i;
    LCD1602_WriteCmd(0x40 | (slot << 3));   /* CGRAM 地址 = slot*8 */
    for (i = 0; i < 8; i++)
    {
        LCD1602_WriteData(pattern[i]);
    }
}

/**
 * @brief  清屏（慢，约2ms；频繁调用是LCD闪烁的主要来源，游戏中避免使用）
 */
void LCD1602_Clear(void)
{
    LCD1602_WriteCmd(0x01);
    LCD1602_DelayMs();
    LCD1602_DelayMs();
}

/**
 * @brief  初始化：8位数据、2行显示、5x7点阵、显示开、光标闪烁关、写入后地址自增
 */
void LCD1602_Init(void)
{
    LCD1602_WriteCmd(0x38);   /* 功能设置 */
    LCD1602_WriteCmd(0x0C);   /* 显示开，光标关，不闪烁 */
    LCD1602_WriteCmd(0x06);   /* 写入后光标右移，画面不动 */
    LCD1602_Clear();          /* 清屏并归位 */
}
