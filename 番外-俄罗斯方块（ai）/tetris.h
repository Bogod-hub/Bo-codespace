#ifndef __TETRIS_H__
#define __TETRIS_H__

/*****************************************************************************
 * tetris.h — 俄罗斯方块游戏逻辑模块
 * 职责：方块生成、移动、旋转(含贴墙踢)、碰撞检测、满行消除、
 *       计分、等级、游戏结束判定、LCD增量渲染
 * 场地：8 列 x 8 行砖格，映射到 LCD 左侧 4x2 字符区
 *       （每个字符=2x4 砖格，8个场地字符恰好用完 CGRAM 8 个自定义槽）
 * 右侧 11 列字符区显示分数/等级/下一块
 *****************************************************************************/

void Tetris_Init(void);                     /* 初始化游戏状态并绘制静态框架 */
void Tetris_OnKey(unsigned char key);       /* 输入按键事件(KEY_LEFT/RIGHT/ROTATE/DROP) */
void Tetris_Update(unsigned int nowMs);     /* 每1ms调用，内部处理重力、长按重复、软降 */
void Tetris_Render(void);                   /* 渲染到LCD（仅状态变化时写屏，防闪烁） */
void Tetris_Restart(void);                  /* 重新开始 */

#endif
