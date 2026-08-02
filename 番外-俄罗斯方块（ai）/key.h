#ifndef __KEY_H__
#define __KEY_H__

/*****************************************************************************
 * key.h — 独立按键驱动（非阻塞状态机 + 软件消抖）
 * 实测普中A2开发板：K1=P3^1，K2=P3^0，K3=P3^2，K4=P3^3（按下为低电平）
 * （与任务预设 P3.2~P3.5 不符，按已验证的真实硬件为准）
 *****************************************************************************/

#include <REGX52.H>

/* ===== 引脚定义（按需修改） ===== */
sbit KEY_K1 = P3^1;     /* K1：左移 */
sbit KEY_K2 = P3^0;     /* K2：右移 */
sbit KEY_K3 = P3^2;     /* K3：旋转 */
sbit KEY_K4 = P3^3;     /* K4：加速下落 */

/* ===== 按键编号 ===== */
#define KEY_NONE    0
#define KEY_LEFT    1   /* K1 左移 */
#define KEY_RIGHT   2   /* K2 右移 */
#define KEY_ROTATE  3   /* K3 旋转 */
#define KEY_DROP    4   /* K4 加速下落 */

/* ===== 功能接口 ===== */
void Key_Init(void);                            /* 初始化（端口置高，准双向输入） */
void Key_Tick(void);                            /* 节拍扫描，每1ms调用一次 */
unsigned char Key_GetEvent(void);               /* 取按键按下事件(消抖后边沿)，无事件返回KEY_NONE */
unsigned int  Key_HeldMs(unsigned char key);    /* 查询该键已持续按住的毫秒数，未按住返回0 */

#endif
