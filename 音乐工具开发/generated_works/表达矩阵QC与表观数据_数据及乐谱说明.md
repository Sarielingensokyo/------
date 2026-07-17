# 表达矩阵 QC 与表观数据：数据及乐谱说明

生成日期：2026-07-17  
平台：BioSound GVR，本地 SoundFont 2 六声部古典配器

## 1. 表达矩阵 QC：PBMC3K

数据来源：10x Genomics 的 PBMC3K 过滤后 gene-barcode UMI 计数矩阵。

- 官方数据页/说明：[10x Genomics PBMC 数据](https://www.10xgenomics.com/datasets/human-pbmc-from-a-healthy-donor-1-k-cells-v-2-2-standard-4-0-0)
- 原始矩阵下载：<https://cf.10xgenomics.com/samples/cell/pbmc3k/pbmc3k_filtered_gene_bc_matrices.tar.gz>
- 原始形状：32,738 个基因 × 2,700 个细胞。
- 平台输入：确定性选择 240 个细胞，保留这些细胞中所有实际表达的 11,914 个基因。
- 保留全部表达基因是必要的：若只保留线粒体基因和少量 HVG，会破坏总 UMI 分母并严重夸大线粒体比例。
- 当前子集平均线粒体比例约 2.24%；完整数据中位数约 2.03%，95% 分位约 4.01%。

平台对应规则：

- 行 = 细胞，数值列 = 基因。
- `MT-*` 列计算线粒体比例，控制全局低通滤波；比例越高，听感越暗。
- 每行非零基因数形成 detected-features 特征。
- 列方差最高的 20 个基因形成当前 MVP 的 HVG 分数，控制前景力度与清晰度。
- 细胞平均表达与总计数参与主旋律和音区组织。

作品设置：C 多利亚、92 BPM、4/4、六声部。GVR 的 `H_scale`、映射、音域、时间线和溯源检查全部通过，无自动修复。

目录：`generated_works/表达矩阵QC_PBMC3K/`

关键文件：

- `PBMC3K_QC平台输入_240细胞.csv`：可直接重新上传平台。
- `PBMC3K_QC处理摘要.json`：选择方法和 QC 摘要。
- `乐谱与音频/PBMC3K_表达矩阵QC_多利亚.musicxml`：MuseScore 可编辑总谱。
- `乐谱与音频/PBMC3K_表达矩阵QC_多利亚.pdf`：PDF 总谱。
- 同目录还包含 MIDI、WAV、Trace CSV、GVR JSON 和作品说明。

## 2. 表观数据：GSM484237 DNA 甲基化

数据来源：NCBI GEO 的正常人体小肠样本 GSM484237；芯片平台为 GPL9183。

- [GSM484237 样本页](https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSM484237)
- [GPL9183 平台注释](https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GPL9183)
- 样本包含 1,413 个研究处理后保留的 CpG 探针 β 值。
- 平台注释已按 `ID_REF/ID` 合并，得到染色体、真实 CpG 坐标、基因、CpG island 和距 TSS 距离。
- β 值范围 0.0183–0.9758，中位数约 0.1827。

平台对应规则：

- 染色体与 CpG 坐标用于恢复基因组顺序并形成音乐时间线。
- `methylation_beta` 经稳健归一化后控制调式音级、力度、结构标记与和声密度。
- 这是一份单一样本，适合展示甲基化轨迹声学化；它不是差异甲基化分析，也不能用于因果推断。

作品设置：D 弗里几亚、76 BPM、4/4、六声部。全部 GVR 规则通过，无自动修复。

目录：`generated_works/表观甲基化_GSM484237/`

关键文件：

- `GSM484237_甲基化平台输入_真实坐标.csv`：可直接重新上传平台。
- `GSM484237_甲基化处理摘要.json`：数据合并和统计摘要。
- `乐谱与音频/GSM484237_小肠甲基化_弗里几亚.musicxml`：MuseScore 可编辑总谱。
- 同目录还包含 MIDI、WAV、Trace CSV、GVR JSON 和作品说明。

MuseScore 已成功生成 PBMC3K 的 PDF。甲基化 MusicXML 本身完整有效，但本机 MuseScore 在第二次命令行转换时无法写入其 AppData 日志目录，因此未自动得到第二份 PDF；在 MuseScore 中打开 MusicXML 后可使用“文件 → 导出 → PDF”。

## 3. 本次同时修复的平台问题

旧分类器把任意列名中含 `methyl` 的表达矩阵都误认为表观数据；真实基因名也可能含该字符串。本次已改为只识别明确的表观字段名（如 `methylation_beta`、`beta_value`、`H3K*`、`ATAC_*`），并加入回归测试。

全部 11 项平台自动测试通过。
