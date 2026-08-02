#ifndef __LCD1602_H__
#define __LCD1602_H__

/*****************************************************************************
 * LCD1602.h — LCD1602 字符液晶底层驱动
 * 引脚宏统一定义在本文件（改硬件接法只需改这里）
 * 实测普中A2开发板：数据口 D0~D7 = P0，RS=P2^6，RW=P2^5，E=P2^7
 * （与任务预设 P2.0~P2.2 不符，P2.0~2.2 在普中A2上是数码管位选锁存，
 *   此处按已验证的真实驱动为准）
 *****************************************************************************/

#include <REGX52.H>

/* ===== 引脚定义（按需修改） ===== */
#define LCD1602_DATA_PORT   P0      /* 8位数据口 */
sbit LCD1602_RS = P2^6;             /* 寄存器选择：0=命令 1=数据 */
sbit LCD1602_RW = P2^5;             /* 读写选择：0=写 */
sbit LCD1602_EN = P2^7;             /* 使能脉冲 */

/* ===== 功能接口 ===== */
void LCD1602_Init(void);                                        /* 初始化：8位、2行、5x7、显示开光标关 */
void LCD1602_WriteCmd(unsigned char cmd);                       /* 写命令 */
void LCD1602_WriteData(unsigned char dat);                      /* 写数据 */
void LCD1602_SetCursor(unsigned char col, unsigned char row);   /* 定位光标 col:0~15 row:0~1 */
void LCD1602_WriteChar(unsigned char col, unsigned char row, unsigned char ch); /* 指定位置写字符(0~7为自定义字模) */
void LCD1602_WriteString(unsigned char col, unsigned char row, char *str);      /* 指定位置写字符串 */
void LCD1602_LoadCGRAM(unsigned char slot, unsigned char *pattern);             /* 向自定义字模槽slot(0~7)写入8字节点阵 */
void LCD1602_Clear(void);                                       /* 清屏(约2ms，游戏中勿频繁调用，会闪烁) */

#endif
