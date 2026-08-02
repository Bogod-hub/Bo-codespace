/*****************************************************************************
 * tetris.c — 俄罗斯方块游戏逻辑实现
 *
 * 显示映射：
 *   场地 8列x8行砖格 → LCD 左侧 4x2 字符区（每个字符 = 2x4 砖格）
 *   场地恰好 8 个字符，与 CGRAM 的 8 个自定义槽一一固定对应：
 *   槽号 i 恒等于场地字符 i，字符编码只需在初始化时写一次，
 *   之后每帧仅重写内容有变化的 CGRAM 槽 → 从机制上避免闪烁。
 *
 * 砖格→字符像素：每砖占 2x2 像素，左右两砖之间留 1 像素间隔
 *   行字节 = (左砖? 0x18:0) | (右砖? 0x03:0)   （bit4=最左像素）
 *
 * 右侧面板（11列，纯内置字符，不占CGRAM）：
 *   行0: S:xxxxx L:x     行1: Next:X
 *   游戏结束: GAME OVER / K3:RESTART
 *****************************************************************************/

#include <REGX52.H>
#include "LCD1602.h"
#include "key.h"
#include "tetris.h"

/* ---------------- 参数配置 ---------------- */
#define FIELD_W        8      /* 场地宽(砖格) */
#define FIELD_H        8      /* 场地高(砖格) */
#define SPAWN_X        2      /* 出生点x(4x4包围盒左缘) */
#define GRAVITY_BASE   500    /* 1级重力间隔(ms) */
#define GRAVITY_STEP   40     /* 每升一级减少(ms) */
#define GRAVITY_MIN    120    /* 重力间隔下限(ms) */
#define SOFTDROP_MS    50     /* K4按住时的下落间隔(ms) */
#define REPEAT_DELAY   220    /* 左右长按首次重复延迟(ms) */
#define REPEAT_RATE    80     /* 左右长按重复间隔(ms) */

/* ---------------- 方块数据 ----------------
 * 7种方块 x 4个旋转态 x 4行，每行低4位有效，bit3=包围盒最左列 */
unsigned char code TETRO[7][4][4] = {
    /* I */ {{0x0,0xF,0x0,0x0},{0x2,0x2,0x2,0x2},{0x0,0x0,0xF,0x0},{0x4,0x4,0x4,0x4}},
    /* O */ {{0x6,0x6,0x0,0x0},{0x6,0x6,0x0,0x0},{0x6,0x6,0x0,0x0},{0x6,0x6,0x0,0x0}},
    /* T */ {{0x4,0xE,0x0,0x0},{0x4,0x6,0x4,0x0},{0x0,0xE,0x4,0x0},{0x4,0xC,0x4,0x0}},
    /* S */ {{0x6,0xC,0x0,0x0},{0x8,0xC,0x4,0x0},{0x6,0xC,0x0,0x0},{0x8,0xC,0x4,0x0}},
    /* Z */ {{0xC,0x6,0x0,0x0},{0x2,0xC,0x8,0x0},{0xC,0x6,0x0,0x0},{0x2,0xC,0x8,0x0}},
    /* L */ {{0x2,0xE,0x0,0x0},{0x4,0x4,0x6,0x0},{0x0,0xE,0x8,0x0},{0xC,0x4,0x4,0x0}},
    /* J */ {{0x8,0xE,0x0,0x0},{0x6,0x4,0x4,0x0},{0x0,0xE,0x2,0x0},{0x4,0x4,0xC,0x0}}
};
char code TETRO_NAME[7] = {'I','O','T','S','Z','L','J'};
/* 一次消除 0/1/2/3/4 行的得分 */
unsigned int code SCORE_TAB[5] = {0, 100, 300, 500, 800};

/* ---------------- 游戏状态 ---------------- */
static unsigned char g_field[FIELD_H];  /* 每字节8位=8列砖格，1=有砖 */
static unsigned char g_type;            /* 当前方块种类 0~6 */
static unsigned char g_rot;             /* 当前旋转态 0~3 */
static signed char   g_x, g_y;          /* 当前方块包围盒左上角 */
static unsigned char g_next;            /* 下一块种类 */
static unsigned int  g_score;           /* 分数 */
static unsigned char g_lines;           /* 累计消行数 */
static unsigned char g_level;           /* 等级(每10行+1) */
static unsigned char g_over;            /* 1=游戏结束 */
static unsigned int  g_lastFall;        /* 上次重力下落时刻(ms) */
static unsigned int  g_lastMove;        /* 上次左右重复移动时刻(ms) */
static unsigned int  g_seed;            /* 伪随机种子 */
static bit           g_dirty;           /* 场地像素有变化，需重写CGRAM */
static bit           g_panelDirty;      /* 面板内容有变化，需重写文字 */

/* ---------------- 内部函数 ---------------- */

/* 取方块包围盒内(r,c)格是否有砖：bit3=最左列 */
static unsigned char CellOf(unsigned char type, unsigned char rot,
                            unsigned char r, unsigned char c)
{
    return (TETRO[type][rot][r] >> (3 - c)) & 0x01;
}

/* 碰撞检测：方块按 rot 放到 (x,y) 是否越界或与场地重叠，1=碰撞 */
static unsigned char Collide(signed char x, signed char y, unsigned char rot)
{
    unsigned char r, c;
    signed char bx, by;
    for (r = 0; r < 4; r++)
    {
        for (c = 0; c < 4; c++)
        {
            if (CellOf(g_type, rot, r, c))
            {
                bx = x + c;
                by = y + r;
                if (bx < 0 || bx >= FIELD_W || by >= FIELD_H)
                    return 1;
                if (by >= 0 && ((g_field[by] >> bx) & 0x01))
                    return 1;
            }
        }
    }
    return 0;
}

/* 伪随机取 0~6：线性同余，种子随节拍与操作时序变化，对玩家等效随机 */
static unsigned char RandType(void)
{
    g_seed = g_seed * 11035u + 12345u;
    return (unsigned char)((g_seed >> 8) % 7);
}

/* 生成新方块；出生点被堵 → 游戏结束 */
static void Spawn(void)
{
    g_type = g_next;
    g_next = RandType();
    g_rot = 0;
    g_x = SPAWN_X;
    g_y = 0;
    if (Collide(g_x, g_y, g_rot))
    {
        g_over = 1;
    }
    g_panelDirty = 1;   /* Next 显示更新 */
}

/* 锁定当前方块：并入场地、消行、计分、生成下一块 */
static void LockPiece(void)
{
    unsigned char r, c, r2, cleared = 0;
    signed char bx, by;

    for (r = 0; r < 4; r++)
    {
        for (c = 0; c < 4; c++)
        {
            if (CellOf(g_type, g_rot, r, c))
            {
                bx = g_x + c;
                by = g_y + r;
                if (by >= 0 && by < FIELD_H && bx >= 0 && bx < FIELD_W)
                    g_field[by] |= (0x01 << bx);
            }
        }
    }

    /* 满行消除：整行 0xFF → 上方整体下移一行 */
    for (r = 0; r < FIELD_H; r++)
    {
        if (g_field[r] == 0xFF)
        {
            for (r2 = r; r2 > 0; r2--)
                g_field[r2] = g_field[r2 - 1];
            g_field[0] = 0x00;
            cleared++;
        }
    }
    if (cleared)
    {
        g_score += SCORE_TAB[cleared];
        g_lines += cleared;
        g_level = g_lines / 10 + 1;
    }

    g_dirty = 1;
    g_panelDirty = 1;
    Spawn();
}

/* 判定场地砖格(bx,by)是否被占用（含正在下落的方块叠加） */
static unsigned char BrickAt(unsigned char bx, unsigned char by)
{
    signed char dx, dy;
    if ((g_field[by] >> bx) & 0x01)
        return 1;
    dx = bx - g_x;
    dy = by - g_y;
    if (dx >= 0 && dx < 4 && dy >= 0 && dy < 4)
        return CellOf(g_type, g_rot, (unsigned char)dy, (unsigned char)dx);
    return 0;
}

/* 由场地状态构建场地字符 slot(0~7) 的 8 字节 CGRAM 点阵 */
static void BuildPattern(unsigned char slot, unsigned char *pat)
{
    unsigned char pr, colL;
    unsigned char baseRow, byte;
    colL = (slot % 4) * 2;          /* 该字符覆盖的砖格列：colL, colL+1 */
    baseRow = (slot / 4) * 4;       /* 该字符覆盖的砖格起始行 */
    for (pr = 0; pr < 8; pr++)      /* pr: 字符内像素行 */
    {
        byte = 0;
        if (BrickAt(colL,     baseRow + pr / 2)) byte |= 0x18;  /* 左砖：像素列0~1 */
        if (BrickAt(colL + 1, baseRow + pr / 2)) byte |= 0x03;  /* 右砖：像素列3~4 */
        pat[pr] = byte;
    }
}

/* 无符号整数转5位十进制字符串(前导零) */
static void NumToStr5(unsigned int n, char *buf)
{
    buf[0] = '0' + (n / 10000) % 10;
    buf[1] = '0' + (n / 1000) % 10;
    buf[2] = '0' + (n / 100) % 10;
    buf[3] = '0' + (n / 10) % 10;
    buf[4] = '0' + n % 10;
    buf[5] = 0;
}

/* 尝试左右移动，dir=-1左/+1右，成功返回1 */
static unsigned char TryMove(signed char dir)
{
    if (!Collide(g_x + dir, g_y, g_rot))
    {
        g_x += dir;
        g_dirty = 1;
        return 1;
    }
    return 0;
}

/* 尝试旋转(含贴墙踢：依次尝试原位、左1、右1、左2、右2) */
static void TryRotate(void)
{
    unsigned char newRot = (g_rot + 1) & 0x03;
    signed char kick;
    for (kick = 0; kick <= 2; kick++)
    {
        if (!Collide(g_x + kick, g_y, newRot)) { g_x += kick; g_rot = newRot; g_dirty = 1; return; }
        if (kick && !Collide(g_x - kick, g_y, newRot)) { g_x -= kick; g_rot = newRot; g_dirty = 1; return; }
    }
}

/* ---------------- 对外接口 ---------------- */

void Tetris_Init(void)
{
    unsigned char i, slot;
    for (i = 0; i < FIELD_H; i++) g_field[i] = 0x00;
    g_score = 0;
    g_lines = 0;
    g_level = 1;
    g_over = 0;
    g_seed = 0x1234;
    g_lastFall = 0;
    g_lastMove = 0;

    /* 场地字符编码与CGRAM槽固定对应，只需写这一次 */
    for (slot = 0; slot < 8; slot++)
    {
        LCD1602_WriteChar(slot % 4, slot / 4, slot);
    }

    g_next = RandType();
    Spawn();
    g_dirty = 1;
    g_panelDirty = 1;
}

void Tetris_OnKey(unsigned char key)
{
    if (g_over)
    {
        if (key == KEY_ROTATE) Tetris_Restart();   /* 结束后 K3 重开 */
        return;
    }
    switch (key)
    {
        case KEY_LEFT:   TryMove(-1); break;
        case KEY_RIGHT:  TryMove(1);  break;
        case KEY_ROTATE: TryRotate(); break;
        case KEY_DROP:   /* 按下立即下落一格，持续加速在 Update 中处理 */
            if (!Collide(g_x, g_y + 1, g_rot)) { g_y++; g_dirty = 1; }
            break;
        default: break;
    }
}

void Tetris_Update(unsigned int nowMs)
{
    unsigned int interval;

    if (g_over) return;
    g_seed++;   /* 种子随时间演化，增强随机性 */

    /* 左右长按重复：按住超过 REPEAT_DELAY 后每 REPEAT_RATE 重复一次 */
    if (Key_HeldMs(KEY_LEFT) > REPEAT_DELAY &&
        (unsigned int)(nowMs - g_lastMove) >= REPEAT_RATE)
    {
        TryMove(-1);
        g_lastMove = nowMs;
    }
    if (Key_HeldMs(KEY_RIGHT) > REPEAT_DELAY &&
        (unsigned int)(nowMs - g_lastMove) >= REPEAT_RATE)
    {
        TryMove(1);
        g_lastMove = nowMs;
    }

    /* 重力：K4按住=软降，否则按等级加速 */
    if (Key_HeldMs(KEY_DROP) > 0)
        interval = SOFTDROP_MS;
    else if (g_level >= 10)
        interval = GRAVITY_MIN;
    else
        interval = GRAVITY_BASE - (unsigned int)(g_level - 1) * GRAVITY_STEP;

    if ((unsigned int)(nowMs - g_lastFall) >= interval)
    {
        g_lastFall = nowMs;
        if (!Collide(g_x, g_y + 1, g_rot))
        {
            g_y++;
        }
        else
        {
            LockPiece();
        }
        g_dirty = 1;
    }
}

void Tetris_Render(void)
{
    unsigned char slot;
    unsigned char pat[8];
    char lineBuf[12];
    char numBuf[6];

    if (!g_dirty && !g_panelDirty) return;   /* 无变化不写屏，防闪烁的关键 */

    if (g_dirty)
    {
        for (slot = 0; slot < 8; slot++)
        {
            BuildPattern(slot, pat);
            LCD1602_LoadCGRAM(slot, pat);
        }
        g_dirty = 0;
    }

    if (g_panelDirty)
    {
        if (g_over)
        {
            LCD1602_WriteString(5, 0, " GAME OVER ");
            LCD1602_WriteString(5, 1, "K3:RESTART ");
        }
        else
        {
            NumToStr5(g_score, numBuf);
            lineBuf[0] = 'S'; lineBuf[1] = ':';
            lineBuf[2] = numBuf[0]; lineBuf[3] = numBuf[1];
            lineBuf[4] = numBuf[2]; lineBuf[5] = numBuf[3];
            lineBuf[6] = numBuf[4];
            lineBuf[7] = ' ';
            lineBuf[8] = 'L'; lineBuf[9] = ':';
            lineBuf[10] = '0' + (g_level > 9 ? 9 : g_level);
            lineBuf[11] = 0;
            LCD1602_WriteString(5, 0, lineBuf);

            lineBuf[0] = 'N'; lineBuf[1] = 'e'; lineBuf[2] = 'x';
            lineBuf[3] = 't'; lineBuf[4] = ':';
            lineBuf[5] = TETRO_NAME[g_next];
            lineBuf[6] = ' '; lineBuf[7] = ' '; lineBuf[8] = ' ';
            lineBuf[9] = ' '; lineBuf[10] = ' '; lineBuf[11] = 0;
            LCD1602_WriteString(5, 1, lineBuf);
        }
        g_panelDirty = 0;
    }
}

void Tetris_Restart(void)
{
    unsigned char i;
    for (i = 0; i < FIELD_H; i++) g_field[i] = 0x00;
    g_score = 0;
    g_lines = 0;
    g_level = 1;
    g_over = 0;
    Spawn();
    g_dirty = 1;
    g_panelDirty = 1;
}
