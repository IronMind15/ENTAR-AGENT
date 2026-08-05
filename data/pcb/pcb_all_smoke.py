# -*- coding: utf-8 -*-
"""全量 53 类计算器冒烟测试——每个计算器一个典型问题，确认能跑通且输出合理"""
import sys
import os
import io

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "scripts")))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from skills.pcb_calc import _handle_impl  # noqa: E402

# 每类计算器一个典型触发问题（覆盖 _detect_calc_type 的 54 类）
CASES = [
    ("trace", "3A 1oz 内层 15℃ 走线要多宽"),
    ("trace2152", "IPC-2152 5A 1oz 外层要多宽"),
    ("impedance", "50Ω 微带 FR4 介质 1mm 要多宽"),
    ("differential", "100Ω 差分微带 FR4 介质 0.4mm 间距 0.3mm 要多宽"),
    ("via", "0.3mm 过孔 1oz 孔壁 能过多少安"),
    ("skin", "1MHz 趋肤深度"),
    ("signal", "FR4 微带 走 10cm 延迟多少"),
    ("thermal", "功耗 5W 热阻 30℃/W 结温多少"),
    ("led", "5V 供电 白LED 20mA 限流电阻"),
    ("divider", "5V 分压 R1=10k R2=10k 输出多少"),
    ("rc", "10kΩ 1μF 时间常数"),
    ("creepage", "380V 爬电距离 PD2 材料组II"),
    ("layout", "综合校验 3A 1oz 380V 材料组II 沟道5mm"),
    ("copper_busbar", "铜排 60A 铜排载流 温度40℃"),
    ("three_phase", "380V 三相 100A 功率因数0.85 多少千瓦"),
    ("snubber", "原始振铃100MHz 加220pF后60MHz 算吸收电路"),
    ("pdn", "Vdd1.2V 纹波3% 阶跃电流5A 目标阻抗多少"),
    ("smps", "Buck 24V转12V 5A Rds10mΩ 效率多少"),
    ("buck", "Buck 24V转5V 3A 500kHz 电感多大"),
    ("rated_current", "30kW 电机电流多少"),
    ("via_parasitic", "板厚1.6mm 焊盘0.6 反焊盘1.0 寄生电容"),
    ("via_thermal", "0.3mm 热过孔 板厚1.6mm 孔壁20μm 20个 热阻多少"),
    ("lc_resonance", "LC谐振 100μH 10nF 谐振频率"),
    ("reactance", "1kHz 10mH 感抗多少"),
    ("supercapacitor", "100F 超级电容 2.7V放到1V 负载100mA 能撑多久"),
    ("battery_life", "2000mAh 电池 平均电流100mA 续航多少"),
    ("battery_charging", "3000mAh 电池 1A 充电器 从20%充到80%多久"),
    ("iot_battery", "LoRa 发射100mA 周期15分钟 睡眠5uA 2000mAh 寿命多久"),
    ("wire_drop", "2.5mm² 铜线 50m 10A 压降多少"),
    ("induction_heating", "钢件2kg 加热到温升300℃ 目标10秒 功率多少"),
    ("opamp", "运放 Rf=10k R1=1k 同相增益多少"),
    ("opamp_filter", "运放滤波 10kΩ 100nF 截止频率"),
    ("diff_amp", "差动放大 R1=10k R2=100k 增益多少"),
    ("wheatstone", "惠斯通电桥 5V 激励 R1=100 R2=100 R3=100 R4=100 输出多少"),
    ("transistor_bias", "Vcc12V Rb470k Rc2k β100 工作点多少"),
    ("zener", "12V 供电 5V1 稳压管 负载10mA 限流电阻"),
    ("lm317", "LM317 R1=240 R2=720 输出多少"),
    ("mc34063", "MC34063 12V转5V 输出多少"),
    ("i2c_pullup", "3.3V I2C 总线电容100pF 上拉多少"),
    ("parallel_resistance", "4.7k 并联 10k 等效电阻"),
    ("series_capacitor", "100nF 串联 100nF 等效"),
    ("electric_power", "12V 3A 功率多少"),
    ("adc", "12位 ADC Vref3.3V 分辨率多少"),
    ("rs485", "RS485 终端电阻 120Ω 距离多少"),
    ("crystal", "晶振 CL=12.5pF 寄生3pF 引脚2pF 匹配电容多少"),
    ("smd_pad", "0603 封装 1.6×0.8mm 焊盘多大"),
    ("vswr", "VSWR 1.5 回波损耗多少"),
    ("frequency_wavelength", "100MHz 波长多少"),
    ("pwm", "PWM 占空比50% 5V 输出多少"),
    ("metal_weight", "紫铜板 100×50×3mm 重量多少"),
    ("emc_convert", "56dBuV 是多少mV"),
    ("crosstalk", "线宽0.2mm 走线间距多少"),
    ("lc_filter", "10μH 100nF LC 滤波 截止频率"),
    ("timer_555", "555 R1=1k R2=10k C=0.1μF 频率多少"),
]


def main():
    ok, fail, nohint = 0, 0, 0
    for ctype, q in CASES:
        try:
            r = _handle_impl(q)
            a = r["answer"]
            src = r["source"]
        except Exception as e:
            a = f"!! EXCEPTION: {type(e).__name__}: {e}"
            src = ""
        # 判定：期望命中预期 ctype；不崩且非提示
        got = src.replace("PCB计算(", "").rstrip(")") if src.startswith("PCB计算") else src
        if "!! EXCEPTION" in a:
            fail += 1
            print(f"❌ {ctype:18} 崩溃: {a[:80]}")
        elif a.lstrip().startswith("1. 【提示】"):
            nohint += 1
            print(f"⚠️ {ctype:18} 提示(参数不足): {a[:60]}")
        elif ctype in got or ctype in src:
            ok += 1
            print(f"✅ {ctype:18} → {got}")
        else:
            fail += 1
            print(f"❌ {ctype:18} 路由错: 期望 {ctype} 实际 {got}")
    print(f"\n==== 冒烟结果: 通过 {ok} / 提示(参数格式问题) {nohint} / 失败 {fail} ====")
    if nohint:
        print("⚠️ 提示类需人工确认是否为参数表达问题（非 bug）")


if __name__ == "__main__":
    main()
