#include <REGX52.H>

void main()
{
	while(1)
	{
		P3_1=1;
		if(P3_1==0)
		{
			P2_0=0;
		}
		else
		{
			P2_1=1;
		}
	}
}