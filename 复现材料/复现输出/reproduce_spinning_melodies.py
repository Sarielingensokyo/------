#!/usr/bin/env python3
"""Rule-based reconstruction of the four Spinning Melodies.

Uses only the Python standard library. The output is MusicXML for MuseScore.
This is a reproducible interpretation of the published rules, not an exact
transcription of John McDonald's unpublished full scores.
"""

from pathlib import Path
from xml.etree.ElementTree import Element, SubElement, ElementTree, indent
import csv

OUT = Path(__file__).resolve().parent

# Sequences recovered from the silk construct table cited by the paper.
H = "MHHHHHHSSGLVPRGSGMKETAAAKFERQHMDSPDLGTDDDDKAMAAS"  # 48 aa
A_CORE = "GAGAAAAAGGAG"  # 12-aa A theme
B_CORE = "QGGYGGLGSQGSGRGGLGGQ"  # 20-aa B theme
LINKER = "TS"

# Scientific pitch. A and T are inferred as documented in README.
PITCH = {
    "T": ("C", 0, 5), "V": ("D", -1, 5),
    "D": ("D", 0, 4), "R": ("D", 0, 5),
    "Q": ("E", -1, 4), "P": ("E", -1, 5),
    "M": ("E", 0, 4), "E": ("E", 0, 5),
    "F": ("F", 0, 5), "K": ("F", 1, 5), "Y": ("F", 1, 6),
    "G": ("G", 0, 5), "L": ("A", -1, 5), "A": ("A", 0, 5),
    "S": ("B", -1, 4), "H": ("B", 0, 4),
}

PIECES = [
    dict(num=1, title="Spinning Melody 1 - Vivo", construct="HAB3",
         fiber="forms fibers", meter=(6, 8), tempo=114,
         form=["H", "A", "B", "B", "B", "TS"]),
    dict(num=2, title="Spinning Melody 2 - Andantino", construct="HA3B",
         fiber="doesn't form fibers", meter=(3, 4), tempo=108,
         form=["H", "A", "A", "A", "B", "TS"]),
    dict(num=3, title="Spinning Melody 3 - Vivo (extended)", construct="HAB3",
         fiber="forms fibers", meter=(6, 8), tempo=116,
         form=["H", "A", "B", "B", "B", "B", "A", "B", "B", "TS"]),
    dict(num=4, title="Spinning Melody 4 - Andantino; Bright (extended)", construct="HA3B",
         fiber="doesn't form fibers", meter=(3, 4), tempo=108,
         form=["H", "A", "A", "A", "B", "A", "B", "A", "A", "TS", "CODA"]),
]

def block_events(label):
    if label == "H":
        return [(aa, 1, "H") for aa in H]  # eighth-note motion
    if label == "TS":
        # Supplement: T=C and S=B-flat; each pitch lasts six quarter beats.
        # With duration units expressed as eighth notes, 6 quarter beats = 12.
        return [("T", 12, "TS"), ("S", 12, "TS")]
    if label == "CODA":
        # Hard-coded restart cue: M-H-H-H = E-B-B-B, in short eighth notes.
        return [(aa, 1, "CODA") for aa in H[:4]]
    core = A_CORE if label == "A" else B_CORE
    return [(aa, 1, label) for aa in core]

def split_for_measures(events, capacity=6):
    """Split notes across barlines and mark ties."""
    out, used = [], 0
    for aa, duration, block in events:
        remaining = duration
        first = True
        while remaining:
            room = capacity - used
            take = min(room, remaining)
            remaining -= take
            tie_start = remaining > 0
            tie_stop = not first
            out.append((aa, take, block, tie_start, tie_stop))
            used += take
            first = False
            if used == capacity:
                out.append((None, 0, "BAR", False, False))
                used = 0
    if used:
        out.append(("REST", capacity-used, "REST", False, False))
        out.append((None, 0, "BAR", False, False))
    return out

def add_pitch(note, aa):
    step, alter, octave = PITCH[aa]
    pitch = SubElement(note, "pitch")
    SubElement(pitch, "step").text = step
    if alter:
        SubElement(pitch, "alter").text = str(alter)
    SubElement(pitch, "octave").text = str(octave)

def note_type(duration):
    return {1:"eighth", 2:"quarter", 3:"quarter", 4:"half", 6:"half"}.get(duration, "whole")

def create_score(piece):
    score = Element("score-partwise", version="4.0")
    work = SubElement(score, "work"); SubElement(work, "work-title").text = piece["title"]
    ident = SubElement(score, "identification")
    creator = SubElement(ident, "creator", type="arranger")
    creator.text = "Rule-based reconstruction from Wong et al. supplementary data"
    plist = SubElement(score, "part-list")
    sp = SubElement(plist, "score-part", id="P1")
    SubElement(sp, "part-name").text = "Flute"
    si = SubElement(sp, "score-instrument", id="P1-I1"); SubElement(si, "instrument-name").text = "Flute"
    mi = SubElement(sp, "midi-instrument", id="P1-I1")
    SubElement(mi, "midi-channel").text = "1"; SubElement(mi, "midi-program").text = "74"
    part = SubElement(score, "part", id="P1")

    events=[]
    for label in piece["form"]: events.extend(block_events(label))
    stream = split_for_measures(events)
    measures=[]; current=[]
    for event in stream:
        if event[0] is None:
            measures.append(current); current=[]
        else: current.append(event)

    prev_block = None
    for idx, bar in enumerate(measures, 1):
        m = SubElement(part, "measure", number=str(idx))
        if idx == 1:
            attrs=SubElement(m,"attributes"); SubElement(attrs,"divisions").text="2"
            key=SubElement(attrs,"key"); SubElement(key,"fifths").text="0"
            tm=SubElement(attrs,"time"); SubElement(tm,"beats").text=str(piece["meter"][0]); SubElement(tm,"beat-type").text=str(piece["meter"][1])
            clef=SubElement(attrs,"clef"); SubElement(clef,"sign").text="G"; SubElement(clef,"line").text="2"
            direction=SubElement(m,"direction",placement="above"); dtype=SubElement(direction,"direction-type")
            met=SubElement(dtype,"metronome"); SubElement(met,"beat-unit").text="quarter"; SubElement(met,"per-minute").text=str(piece["tempo"])
            SubElement(direction,"sound",tempo=str(piece["tempo"]))
        for aa,dur,block,tie_start,tie_stop in bar:
            if block != prev_block and block not in ("REST", "TS"):
                direction=SubElement(m,"direction",placement="above"); dt=SubElement(direction,"direction-type")
                SubElement(dt,"words").text = block
                prev_block=block
            note=SubElement(m,"note")
            if aa == "REST": SubElement(note,"rest")
            else: add_pitch(note,aa)
            SubElement(note,"duration").text=str(dur)
            SubElement(note,"voice").text="1"; SubElement(note,"type").text=note_type(dur)
            if dur in (3,6): SubElement(note,"dot")
            if tie_stop: SubElement(note,"tie",type="stop")
            if tie_start: SubElement(note,"tie",type="start")
            if tie_start or tie_stop:
                nots=SubElement(note,"notations")
                if tie_stop: SubElement(nots,"tied",type="stop")
                if tie_start: SubElement(nots,"tied",type="start")
            lyric=SubElement(note,"lyric",number="1"); SubElement(lyric,"text").text = aa if aa != "REST" else ""
        if idx == len(measures):
            barline=SubElement(m,"barline",location="right"); SubElement(barline,"bar-style").text="light-heavy"

    indent(score, space="  ")
    path=OUT/f"{piece['num']:02d}_{piece['construct']}_rule_reconstruction.musicxml"
    ElementTree(score).write(path,encoding="utf-8",xml_declaration=True)
    return path, events, len(measures)

def main():
    OUT.mkdir(parents=True,exist_ok=True)
    rows=[]
    for p in PIECES:
        path,events,bars=create_score(p)
        rows.append([p["num"],p["construct"],p["fiber"],p["meter"][0],p["meter"][1],p["tempo"],"-".join(p["form"]),len(events),bars,path.name])
        print(path)
    with (OUT/"manifest.csv").open("w",newline="",encoding="utf-8-sig") as f:
        w=csv.writer(f); w.writerow(["piece","construct","fiber_result","beats","beat_type","tempo_qpm","form","events_before_tie_split","measures","musicxml"]); w.writerows(rows)
    with (OUT/"pitch_mapping.csv").open("w",newline="",encoding="utf-8-sig") as f:
        w=csv.writer(f); w.writerow(["amino_acid","step","alter","octave","status"])
        for aa,(step,alter,octave) in PITCH.items():
            status="inferred" if aa=="A" else ("explicit_from_TS" if aa=="T" else "explicit")
            w.writerow([aa,step,alter,octave,status])

if __name__ == "__main__": main()
