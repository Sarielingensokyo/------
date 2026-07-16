from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import streamlit as st

from biomusic.codec import decode_artifact
from biomusic.exporters import ORCHESTRAL_LABELS, musicxml_to_pdf
from biomusic.parsers import parse_uploaded
from biomusic.pipeline import SonificationSettings, run_pipeline


ROOT = Path(__file__).resolve().parent
EXAMPLES = ROOT / "examples"

st.set_page_config(page_title="BioSound GVR", page_icon="🧬", layout="wide")
st.markdown(
    """
    <style>
    :root { --ink:#102a43; --teal:#087f8c; --lime:#a8c256; --paper:#f6f8f5; --field:#fffdf5; }
    .stApp { background: linear-gradient(135deg, #f7faf8 0%, #eef6f7 55%, #f7f4ec 100%); }
    .stApp, .stApp p, .stApp li, .stApp label, .stApp h1, .stApp h2, .stApp h3 { color:var(--ink); }
    [data-testid="stSidebar"] { background:#102a43; }
    [data-testid="stSidebar"] h1, [data-testid="stSidebar"] h2,
    [data-testid="stSidebar"] h3, [data-testid="stSidebar"] label,
    [data-testid="stSidebar"] p, [data-testid="stSidebar"] small { color:#f6fbfb !important; }
    [data-testid="stSidebar"] input, [data-testid="stSidebar"] textarea,
    [data-testid="stSidebar"] div[data-baseweb="select"] > div {
        background:var(--field) !important; color:var(--ink) !important;
        border:1px solid #79a9a4 !important; border-radius:8px;
    }
    [data-testid="stSidebar"] input::placeholder, [data-testid="stSidebar"] textarea::placeholder { color:#627d98 !important; }
    [data-testid="stSidebar"] div[data-baseweb="select"] span,
    [data-testid="stSidebar"] div[data-baseweb="select"] svg { color:var(--ink) !important; fill:var(--ink) !important; }
    [data-testid="stSidebar"] [data-testid="stFileUploaderDropzone"] {
        background:#e9f2ef !important; border:1px dashed #4d7c78 !important;
    }
    [data-testid="stSidebar"] [data-testid="stFileUploaderDropzone"] * { color:var(--ink) !important; }
    div[data-baseweb="popover"], ul[role="listbox"] { background:#fffdf8 !important; }
    li[role="option"], li[role="option"] * { color:var(--ink) !important; }
    textarea, input { background:var(--field) !important; color:var(--ink) !important; }
    [data-testid="stTextArea"] textarea, [data-testid="stTextInput"] input {
        border:1px solid #4d7c78 !important; caret-color:var(--ink) !important;
    }
    [data-testid="stDataFrame"] { background:#ffffff; border:1px solid #c9d8d6; }
    .hero { padding:1.5rem 1.7rem; border:1px solid #c9dedb; border-radius:18px;
            background:rgba(255,255,255,.82); box-shadow:0 12px 32px rgba(16,42,67,.08); }
    .hero h1 { color:#102a43; margin:0 0 .35rem 0; letter-spacing:-.035em; }
    .hero p { color:#486581; margin:0; max-width:900px; }
    .badge { display:inline-block; color:#07656f; background:#dff3f1; border-radius:999px;
             padding:.2rem .65rem; margin:.8rem .4rem 0 0; font-size:.82rem; }
    .boundary { border-left:5px solid #d99b35; padding:.8rem 1rem; background:#fff7e7; border-radius:8px; }
    div[data-testid="stMetric"] { background:rgba(255,255,255,.8); padding:.8rem; border-radius:12px; border:1px solid #d9e5e4; }
    </style>
    <div class="hero">
      <h1>BioSound GVR</h1>
      <p>把序列、结构与多组学指标编译为可追溯的多声部古典室内乐。主旋律、对位、低音、和声场与结构重音均有独立乐器、独立 MIDI 通道和独立谱表。</p>
      <span class="badge">FASTA · PDB · CSV · 文本序列</span><span class="badge">Classical orchestration</span>
      <span class="badge">MIDI · MusicXML · PDF</span><span class="badge">Auditable trace</span>
    </div>
    """,
    unsafe_allow_html=True,
)


@st.cache_data(show_spinner=False)
def load_example(name: str) -> tuple[str, bytes]:
    path = EXAMPLES / name
    return path.name, path.read_bytes()


with st.sidebar:
    st.header("输入与作曲设置")
    source = st.radio("数据来源", ["示例数据", "粘贴序列", "上传文件"], horizontal=True)
    if source == "示例数据":
        example_display = st.selectbox("选择示例", ["蛋白质 FASTA", "带坐标的 PDB", "单细胞表达 CSV"])
        example_label = {
            "蛋白质 FASTA": "example_protein.fasta",
            "带坐标的 PDB": "example_structure.pdb",
            "单细胞表达 CSV": "example_expression.csv",
        }[example_display]
        filename, file_bytes = load_example(example_label)
    elif source == "粘贴序列":
        sequence_name = st.text_input("序列名称", value="pasted_sequence")
        sequence_text = st.text_area(
            "直接粘贴 FASTA 或纯序列",
            height=180,
            placeholder="例如：MKTIIALSYIFCLVFADYKDDDDK\n也可以粘贴以 > 开头的完整 FASTA",
        )
        if sequence_text.strip():
            fasta_text = sequence_text.strip()
            if not fasta_text.startswith(">"):
                fasta_text = f">{sequence_name.strip() or 'pasted_sequence'}\n{fasta_text}"
            filename, file_bytes = ("pasted_sequence.fasta", fasta_text.encode("utf-8"))
        else:
            filename, file_bytes = ("", b"")
    else:
        upload = st.file_uploader("上传 FASTA、PDB 或 CSV", type=["fasta", "fa", "faa", "fna", "pdb", "csv"])
        filename, file_bytes = (upload.name, upload.getvalue()) if upload else ("", b"")

    forced_label = st.selectbox("序列类型", ["自动识别", "蛋白质", "DNA", "RNA"])
    forced_type = {"自动识别": "auto", "蛋白质": "protein", "DNA": "dna", "RNA": "rna"}[forced_label]

    record_index = 0
    if file_bytes:
        try:
            previews = parse_uploaded(filename, file_bytes, forced_type)
            if len(previews) > 1:
                record_index = st.selectbox("记录/链", range(len(previews)), format_func=lambda i: previews[i].name)
            else:
                st.caption(f"记录：{previews[0].name} · {previews[0].length} 个数据点")
        except Exception as exc:
            st.error(str(exc))

    pitch_mode = st.selectbox("音高策略", ["生物物理映射", "文献氨基酸映射", "可逆十二音列编解码"])
    st.caption("所有试听与 MIDI 音色仅使用古典管弦乐器：木管、弦乐、圆号和竖琴。")
    scale_name = st.selectbox("调式", ["多利亚调式", "五声音阶", "自然小调", "半音阶"])
    row_form = st.selectbox("十二音列呈现形式", ["P", "I", "R", "RI"], disabled=pitch_mode != "可逆十二音列编解码")
    if pitch_mode == "可逆十二音列编解码":
        st.caption("DNA/RNA 每 12 个碱基、蛋白质每 6 个残基编码为一条音列；P/I/R/RI 会写入元数据并在解码前逆变换。")
    tempo = st.slider("速度（四分音符/分钟）", 48, 160, 96)
    meter = st.selectbox("拍号", ["4/4", "3/4", "6/8", "5/4"])
    meter_beats, meter_beat_type = map(int, meter.split("/"))
    max_events = st.slider(
        "最多生成事件", 24, 600, 240, step=12,
        disabled=pitch_mode == "可逆十二音列编解码",
        help="可逆模式必须保留全部载体音符，因此不会抽样；此上限仅用于其他映射模式。",
    )
    texture_density = st.slider(
        "古典编配层数",
        1, 6, 6,
        help="1=生物主旋律；2=加弦乐对位；3=加大提琴低音；4=加圆号；5=加中提琴；6=加竖琴结构重音。",
    )
    counterpoint_strength = st.slider(
        "对位独立度",
        0.0, 1.0, 0.70, 0.05,
        help="越高，弦乐声部越倾向反向音区和延迟轮唱；生物来源仍由溯源字段保留。",
        disabled=texture_density < 2,
    )
    seed = st.number_input("可复现随机种子", min_value=0, max_value=999999, value=42)
    enable_nma = st.checkbox("PDB 输入计算粗粒化 NMA", value=True)
    run = st.button("生成并验证", type="primary", use_container_width=True, disabled=not file_bytes)


if run:
    settings = SonificationSettings(
        forced_type=forced_type,
        record_index=int(record_index),
        pitch_mode=pitch_mode,
        scale_name=scale_name,
        tempo=tempo,
        meter_beats=meter_beats,
        meter_beat_type=meter_beat_type,
        max_events=max_events,
        row_form=row_form,
        seed=int(seed),
        enable_nma=enable_nma,
        texture_density=int(texture_density),
        counterpoint_strength=float(counterpoint_strength),
    )
    try:
        with st.spinner("解析数据、计算特征并执行 GVR…"):
            st.session_state["result"] = run_pipeline(filename, file_bytes, settings)
            st.session_state.pop("score_pdf", None)
            st.session_state.pop("score_pdf_message", None)
    except Exception as exc:
        st.error(f"生成失败：{exc}")


with st.expander("从平台产物还原 DNA / RNA / 蛋白质序列", expanded=False):
    st.caption("严格解码平台导出的 GVR JSON、MusicXML 或 MIDI。WAV 不作为无损载体；非法音列不会被静默修复。")
    encoded_upload = st.file_uploader(
        "上传待解码文件", type=["json", "musicxml", "xml", "mid", "midi"], key="codec_decoder"
    )
    if encoded_upload and st.button("严格验证并解码", key="decode_codec"):
        try:
            decoded_sequence, decoded_meta, decoded_rows = decode_artifact(encoded_upload.name, encoded_upload.getvalue())
            st.session_state["decoded_codec"] = (decoded_sequence, decoded_meta, decoded_rows)
        except Exception as exc:
            st.session_state.pop("decoded_codec", None)
            st.error(f"解码失败：{exc}")
    if st.session_state.get("decoded_codec"):
        decoded_sequence, decoded_meta, decoded_rows = st.session_state["decoded_codec"]
        st.success(f"校验通过：恢复 {len(decoded_sequence)} 个符号，共 {len(decoded_rows)} 个载体块。")
        st.code(decoded_sequence)
        extension = "faa" if decoded_meta["data_type"] == "protein" else ("fna" if decoded_meta["data_type"] == "dna" else "fa")
        fasta = f">decoded_{decoded_meta['data_type']}\n{decoded_sequence}\n".encode("utf-8")
        st.download_button("下载还原 FASTA", fasta, f"decoded_sequence.{extension}", "text/plain")
        with st.expander("编解码元数据"):
            st.json(decoded_meta)


result = st.session_state.get("result")
if result is None:
    st.info("请从左侧选择示例或上传文件，然后点击“生成并验证”。所有计算都在本机完成。")
    st.markdown(
        """
        ### 映射路线

        **一维序列**决定音乐时间线；**理化性质与 QC**控制音域、时值、力度、音色与滤波；
        **PDB 坐标和接触度**控制左右声像与音域张力；**GVR**阻止错误映射或不可追溯事件进入下载结果。
        """
    )
    st.stop()


summary = result.summary
cols = st.columns(6)
cols[0].metric("输入点数", summary["source_items"])
cols[1].metric("音乐事件", summary["musical_events"])
cols[2].metric("GVR", "通过" if summary["gvr_passed"] else "未通过")
cols[3].metric("自动修复", summary["repairs"])
cols[4].metric("音频时长", f"{result.audio_info['duration_seconds']:.1f} s")
cols[5].metric("独立声部", summary["voice_count"])

workbench, trace_tab, mapping_tab, science_tab = st.tabs(["试听与导出", "生物—音乐轨迹", "GVR 与映射", "科学边界"])

with workbench:
    left, right = st.columns([1.35, 1])
    with left:
        st.subheader(result.record.name)
        st.audio(result.wav, format="audio/wav")
        st.caption(
            f"{summary['voice_count']} 声部古典室内乐预览 · 可同时发声；QC 低通截止频率 {result.audio_info['qc_lowpass_cutoff_hz']} Hz。"
            + (" 音频因长度上限被截断，谱面和轨迹仍保留全部事件。" if result.audio_info["truncated"] else "")
        )
        st.subheader("当前编制")
        orchestration_rows = [
            {
                "声部": voice_id,
                "功能": info["role"],
                "乐器": ORCHESTRAL_LABELS.get(info["instrument"], info["instrument"]),
                "事件数": info["events"],
            }
            for voice_id, info in summary["voices"].items()
        ]
        st.dataframe(pd.DataFrame(orchestration_rows), use_container_width=True, hide_index=True)
        feature_names = [k for k, v in result.record.features.items() if len(v) == result.record.length][:4]
        if feature_names:
            chart = pd.DataFrame({k: result.record.features[k][:300] for k in feature_names})
            chart.index.name = "数据位置"
            st.line_chart(chart)
    with right:
        st.subheader("下载可复现产物")
        safe_name = "biosound_" + "".join(c if c.isalnum() else "_" for c in result.record.name)[:40]
        st.download_button("下载 WAV 音频", result.wav, f"{safe_name}.wav", "audio/wav", use_container_width=True)
        st.download_button("下载 MIDI", result.midi, f"{safe_name}.mid", "audio/midi", use_container_width=True)
        st.download_button("下载 MusicXML", result.musicxml, f"{safe_name}.musicxml", "application/vnd.recordare.musicxml+xml", use_container_width=True)
        st.download_button("下载音符溯源 CSV", result.trace_csv, f"{safe_name}_trace.csv", "text/csv", use_container_width=True)
        st.download_button("下载 GVR 报告 JSON", result.report_json, f"{safe_name}_gvr.json", "application/json", use_container_width=True)
        manual_path = ROOT / "BioSound_GVR可逆十二音列版平台功能与原理说明.docx"
        if manual_path.exists():
            st.download_button(
                "下载完整平台说明手册",
                manual_path.read_bytes(),
                manual_path.name,
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                use_container_width=True,
            )
        if st.button("用本机 MuseScore 生成 PDF 乐谱", use_container_width=True):
            with st.spinner("正在调用 MuseScore 排版…"):
                pdf, message = musicxml_to_pdf(result.musicxml)
                st.session_state["score_pdf"] = pdf
                st.session_state["score_pdf_message"] = message
        if st.session_state.get("score_pdf"):
            st.download_button("下载 PDF 乐谱", st.session_state["score_pdf"], f"{safe_name}.pdf", "application/pdf", use_container_width=True)
        if st.session_state.get("score_pdf_message"):
            st.caption(st.session_state["score_pdf_message"])

with trace_tab:
    st.subheader("每个音符如何从生物数据产生")
    trace_rows = []
    for event in result.events:
        row = event.to_dict()
        trace_rows.append({
            "音符ID": event.event_id,
            "声部": event.voice_id,
            "音乐功能": event.role,
            "主事件ID": event.parent_event_id,
            "来源位置": event.source_label,
            "符号": event.symbol,
            "起拍": event.onset,
            "时值": event.duration,
            "MIDI": event.midi,
            "音级": event.midi % 12,
            "声像": round(event.pan, 3),
            "古典配器": ORCHESTRAL_LABELS.get(event.timbre, event.timbre),
            "状态": event.status,
            "映射理由": event.mapping_rule,
        })
    st.dataframe(pd.DataFrame(trace_rows), use_container_width=True, height=430)
    with st.expander("输入元数据"):
        st.json(result.record.metadata)

with mapping_tab:
    left, right = st.columns(2)
    with left:
        st.subheader("最终发布检查")
        check_df = pd.DataFrame([{"规则": k, "结果": "通过" if v else "失败"} for k, v in result.report.checks.items()])
        st.dataframe(check_df, use_container_width=True, hide_index=True)
        if result.report.tone_row:
            st.code("载体块 1: " + " – ".join(map(str, result.report.tone_row)))
        if result.report.tone_rows and len(result.report.tone_rows) > 1:
            st.caption(f"共 {len(result.report.tone_rows)} 个独立载体块；完整音列保存在 GVR JSON、MusicXML 和 MIDI 中。")
    with right:
        st.subheader("修复日志")
        if result.report.repairs:
            st.dataframe(pd.DataFrame([r.to_dict() for r in result.report.repairs]), use_container_width=True)
        else:
            st.success("本次候选事件无需修复。")
    st.subheader("当前氨基酸音高配置（配器独立由理化特征决定）")
    st.dataframe(pd.read_csv(ROOT / "config" / "pitch_mapping.csv"), use_container_width=True, height=310)

with science_tab:
    st.markdown(
        """
        <div class="boundary"><b>重要：</b>这里生成的是“数据声学化模型”，不是分子发出的真实录音。
        音高、音色和空间声像都由公开规则映射；它们适合比较、教学和假设探索，不替代结构测定或生物统计检验。</div>
        """,
        unsafe_allow_html=True,
    )
    st.subheader("NMA 状态")
    st.json(result.nma)
    st.subheader("QC 声学审计")
    st.write(
        "CSV 会先依据列名区分表达矩阵、表观信号、代谢丰度、质谱峰与 GWAS/EWAS 类关联景观；无法识别时才启用“行为细胞、列为基因”的表达矩阵假设。"
        "在线粒体比例可计算时，它会降低试听的低通截止频率；HVG 分数控制前景清晰度与力度。正式分析前仍须核对表格方向、标准化和字段含义。"
    )
    st.subheader("论文方法的实现边界")
    st.write(
        "平台采用十二音列论文的事件记录、确定性验证、局部修复、最终发布门控和可审计轨迹，并依据《Transform from genes to music》综述实现模态特异的分层编配。"
        "当前生成器是可复现规则系统，不调用在线 LLM；启动时不需要邮箱、账号、API 密钥或网络连接。"
    )
    if result.report.codec:
        st.subheader("可逆载体边界")
        st.write(
            "只有 V1 生物主旋律的连续十二音级块参与解码；对位、低音、圆号、中提琴与竖琴均为表现层。"
            "未修改且保留元数据的 JSON、MusicXML、MIDI 可严格往返；WAV、丢失元数据或越界编辑不承诺无损。"
        )
