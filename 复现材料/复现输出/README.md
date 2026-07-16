# 丝蛋白 Spinning Melodies：规则复现版

## 复现性质

这是依据补充材料和构建体序列制作的“规则复现版”，不是四首原始录音的逐音自动转录。补充材料明确允许音区、节拍、力度、时值、呼吸和 A/B 主题顺序发生作曲性变化，但未提供完整总谱，因此无法仅凭 PDF 唯一恢复原谱。

## 数据依据

- His-tag（48 aa）：`MHHHHHHSSGLVPRGSGMKETAAAKFERQHMDSPDLGTDDDDKAMAAS`
- A 主题（12 aa）：`GAGAAAAAGGAG`
- B 主题（20 aa）：`QGGYGGLGSQGSGRGGLGGQ`
- 构建体：HAB3 与 HA3B
- 1、3 号：6/8，Vivo，四分音符 112-116
- 2、4 号：3/4，Andantino，四分音符 108
- TS：T=C、S=B-flat，各持续 6 个四分音符拍

## 两个必要推断与物理意义

1. 补充材料以 TS linker 明示 T=C、S=B-flat，因此 T 的音高可确定。
2. 键表覆盖十二平均律除 A 以外的其余音级，Ala（A）又大量存在于构建体中，因此本复现将 A 映射到 A 音。这是结构性推断，已在 `pitch_mapping.csv` 标记为 `inferred`。

### Ala 映射到 A 音的声学化逻辑

Ala（A）是疏水性 A 区块及 beta-折叠纳米晶形成的重要组成。将其映射为 A 音，既保留了字母上的直觉对应，也使 A 核心 `GAGAAAAAGGAG` 呈现以 A 音为中心、重复度很高的局域化音高单元。其受限、密集且反复回归的听觉特征，可作为疏水区局部强烈聚集、但未必促进整体纤维延伸这一机制的音乐类比。由于作品采用单声部长笛，这里更准确地称为“局域化紧张音高簇/持续音型”，而非同时发声的和弦。该解释属于声化设计上的物理启发，不是对材料行为的独立实验证明。

## 四首的确定性形式

- 01（Vivo，6/8）：`HAB3`，Melody 1，形成纤维；`H-A-B-B-B-TS`
- 02（Andantino，3/4）：`HA3B`，Melody 1，不形成纤维；`H-A-A-A-B-TS`
- 03（Vivo，6/8，加长版）：`HAB3`，Melody 2，形成纤维；`H-A-B-B-B-B-A-B-B-TS`
- 04（Andantino，3/4，加长版）：`HA3B`，Melody 2，不形成纤维；`H-A-A-A-B-A-B-A-A-TS-CODA`

所有普通氨基酸先按最常见的八分音符运动实现。01、02、03 在曲末显式追加 TS；04 在 TS 后追加 coda。TS 的 T、S 两音各保持 6 个四分音符拍，合计 12 个四分音符拍。第 3、4 首的加长形式是可复跑的作曲选择，不声称与原作逐音相同。

第 4 首 coda 硬性采用 His-tag 起点的前四个氨基酸 `M-H-H-H`，按音高键映射为 `E-B-B-B`，使用四个短促八分音符。这一未解决音型指向第 1 首开头，对应补充材料所说的“威胁要让整个过程重新开始”。

## 运行

```powershell
python reproduce_spinning_melodies.py
```

随后用 MuseScore 4 打开 `.musicxml`，或运行本文件夹中的 `export_with_musescore.ps1` 导出 MSCZ、PDF、MIDI 和 WAV。
