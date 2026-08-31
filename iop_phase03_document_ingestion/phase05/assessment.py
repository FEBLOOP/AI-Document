from __future__ import annotations
import json
import os
from typing import Any


SCORING_GUIDE = {"1": "แทบไม่มีหลักฐาน", "2": "มีบางส่วน", "3": "มีส่วนใหญ่", "4": "ครบถ้วน", "5": "ครบถ้วนและเป็นต้นแบบ"}


def review_packet(criterion: dict[str, Any], evidence: list[dict[str, Any]]) -> dict[str, Any]:
    return {"criterion": criterion, "evidence": evidence,
            "instructions": "ใช้เฉพาะหลักฐานที่ให้มา ห้ามแต่งข้อมูลหรืออ้างหน้า/บล็อกที่ไม่มีใน evidence. "
                            "คะแนน 1-5 ตาม scoring_guide และต้องระบุ evidence_chunk_ids, strengths, missing_points."}


def assess_openai(packet: dict[str, Any], model: str) -> dict[str, Any]:
    from openai import OpenAI
    prompt = json.dumps({"scoring_guide": SCORING_GUIDE, **packet}, ensure_ascii=False)
    response = OpenAI(api_key=os.environ.get("OPENAI_API_KEY")).chat.completions.create(
        model=model, temperature=0,
        response_format={"type": "json_object"},
        messages=[{"role": "system", "content": "You are a careful Thai IOP assessor. Return only JSON with score (integer 1-5), confidence (0-1), rationale_th, evidence_chunk_ids, strengths, missing_points."},
                  {"role": "user", "content": prompt}])
    assessment = json.loads(response.choices[0].message.content)
    if not isinstance(assessment.get("score"), int) or not 1 <= assessment["score"] <= 5:
        raise ValueError("LLM returned an invalid score.")
    assessment["confidence"] = max(0, min(1, float(assessment.get("confidence", 0))))
    return assessment
