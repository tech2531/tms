#!/usr/bin/env python3
"""
한국환경공단 CleanSYS 실시간 측정결과(rltmMesureResult) 자동 수집기
- GitHub Actions에서 매 30분마다 실행되어 data/YYYY-MM-DD.json에 누적 저장
- 외부 패키지 의존성 없음 (Python 표준 라이브러리 전용)
- API 인증키는 환경변수 CLEANSYS_API_KEY (GitHub Secrets)에서 안전하게 로드
"""

import json
import os
import re
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))
DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
ENDPOINT = "https://apis.data.go.kr/B552584/cleansys/rltmMesureResult"

TARGET_STACKS = [
    {"id": "dk-1", "corpKey": "동국제강", "corpLabel": "동국제강(주)", "stackCode": 1},
    {"id": "dk-2", "corpKey": "동국제강", "corpLabel": "동국제강(주)", "stackCode": 2},
    {"id": "dk-7", "corpKey": "동국제강", "corpLabel": "동국제강(주)", "stackCode": 7},
    {"id": "dk-9", "corpKey": "동국제강", "corpLabel": "동국제강(주)", "stackCode": 9},
    {"id": "dk-11", "corpKey": "동국제강", "corpLabel": "동국제강(주)", "stackCode": 11},
    {"id": "hd-3", "corpKey": "현대제철", "corpLabel": "현대제철(주)", "stackCode": 3},
    {"id": "hd-4", "corpKey": "현대제철", "corpLabel": "현대제철(주)", "stackCode": 4},
    {"id": "hd-5", "corpKey": "현대제철", "corpLabel": "현대제철(주)", "stackCode": 5},
    {"id": "hd-6", "corpKey": "현대제철", "corpLabel": "현대제철(주)", "stackCode": 6},
    {"id": "hd-7", "corpKey": "현대제철", "corpLabel": "현대제철(주)", "stackCode": 7},
    {"id": "hd-10", "corpKey": "현대제철", "corpLabel": "현대제철(주)", "stackCode": 10},
    {"id": "hd-11", "corpKey": "현대제철", "corpLabel": "현대제철(주)", "stackCode": 11},
    {"id": "hd-12", "corpKey": "현대제철", "corpLabel": "현대제철(주)", "stackCode": 12},
    {"id": "hd-14", "corpKey": "현대제철", "corpLabel": "현대제철(주)", "stackCode": 14},
    {"id": "ts-1", "corpKey": "대한제당", "corpLabel": "대한제당(주)", "stackCode": 1},
    {"id": "cj-1", "corpKey": "제일제당", "corpLabel": "씨제이제일제당(주)", "stackCode": 1},
    {"id": "dw-522", "corpKey": "동화", "corpLabel": "동화기업(주)", "stackCode": 522},
    {"id": "ak-4", "corpKey": "앰코", "corpLabel": "앰코테크놀로지코리아(주)", "stackCode": 4},
    {"id": "ak-5", "corpKey": "앰코", "corpLabel": "앰코테크놀로지코리아(주)", "stackCode": 5},
]

FALLBACK_KEYWORDS = {
    "동국제강": ["동국제강"],
    "현대제철": ["현대제철"],
    "대한제당": ["대한제당"],
    "제일제당": ["씨제이제일제당", "제일제당"],
    "동화": ["동화기업", "대성동화"],
    "앰코": ["앰코테크놀로지"],
}


def now_kst() -> datetime:
    return datetime.now(KST)


def match_canonical_corp(raw_name: str):
    if not raw_name:
        return None
    s = re.sub(r"\s+", "", str(raw_name))
    if "동국제강" in s:
        return "동국제강"
    if "현대제철" in s:
        return "현대제철"
    if "대한제당" in s:
        return "대한제당"
    if "제일제당" in s or "CJ제일제당" in s or "씨제이제일제당" in s:
        return "제일제당"
    if "동화기업" in s or "대성동화" in s:
        return "동화"
    if "앰코테크놀로지" in s or "앰코" in s:
        return "앰코"
    return None


def parse_clean_stack_number(raw_stack):
    if raw_stack is None:
        return None
    cleaned = re.sub(r"제?\s*\d+\s*공장", "", str(raw_stack))
    cleaned = re.sub(r"\d+\s*호기", "", cleaned).strip()
    m = re.search(r"0*(\d+)", cleaned)
    return int(m.group(1)) if m else None


def parse_mesure_dt(raw_dt):
    """측정일시 문자열을 (YYYY-MM-DD, HH:MM)으로 정규화"""
    if not raw_dt:
        return None, None
    digits = re.sub(r"\D", "", str(raw_dt))
    current = now_kst()
    if len(digits) >= 12:
        yyyy, mm, dd, hh, mi = digits[0:4], digits[4:6], digits[6:8], digits[8:10], digits[10:12]
    elif len(digits) == 8:
        yyyy = current.strftime("%Y")
        mm, dd, hh, mi = digits[0:2], digits[2:4], digits[4:6], digits[6:8]
    elif len(digits) == 4:
        yyyy, mm, dd = current.strftime("%Y"), current.strftime("%m"), current.strftime("%d")
        hh, mi = digits[0:2], digits[2:4]
    else:
        return None, None

    # 30분 슬롯 검증 (00 또는 30이 아니더라도 원본 분 유지하되 표준 범위 확인)
    if not ("00" <= hh <= "23" and "00" <= mi <= "59"):
        return None, None
    return f"{yyyy}-{mm}-{dd}", f"{hh}:{mi}"


def extract_strict_nox(item: dict):
    """타 오염물질 폴백 없이 순수 질소산화물(NOx) 측정값 및 상태 추출"""
    nox_keys = ("nox_mesure_value", "noxMesureValue", "nox_val", "noxVal")
    for k in nox_keys:
        if k in item:
            raw = item.get(k)
            if raw is None or str(raw).strip() == "":
                return None, "미측정/대기", ""
            s = str(raw).replace(",", "").strip()
            try:
                val = round(float(s), 2)
                return val, "정상가동", s
            except ValueError:
                return None, s, s

    # 항목 구분형 응답 대비
    item_nm = str(item.get("item_nm") or item.get("msr_item_nm") or "").upper()
    if "질소" in item_nm or "NOX" in item_nm:
        raw = item.get("mesure_value") or item.get("mesureValue")
        if raw is not None and str(raw).strip() != "":
            s = str(raw).replace(",", "").strip()
            try:
                return round(float(s), 2), "정상가동", s
            except ValueError:
                return None, s, s

    return None, None, ""


def extract_strict_nox_std(item: dict):
    for k in ("nox_exhst_perm_stdr_value", "noxExhstPermStdrValue"):
        raw = item.get(k)
        if raw is not None and str(raw).strip() != "":
            try:
                val = float(str(raw).replace(",", "").strip())
                if val > 0:
                    return int(val) if val.is_integer() else round(val, 2)
            except ValueError:
                pass
    return None


def parse_response_body(text: str):
    text = text.strip()
    if not text:
        return []

    # JSON 파싱
    if text.startswith("{") or text.startswith("["):
        try:
            data = json.loads(text)
            body = data.get("response", {}).get("body", {}) if isinstance(data, dict) else {}
            items = body.get("items", [])
            if isinstance(items, dict):
                items = items.get("item", [])
            if isinstance(items, dict):
                return [items]
            if isinstance(items, list):
                return items
        except Exception as e:
            print(f"[WARN] JSON 파싱 오류: {e}")

    # XML 파싱
    if text.startswith("<"):
        try:
            root = ET.fromstring(text)
            rows = []
            for item_el in root.findall(".//item"):
                row = {}
                for child in item_el:
                    row[child.tag] = (child.text or "").strip()
                if row:
                    rows.append(row)
            return rows
        except Exception as e:
            print(f"[WARN] XML 파싱 오류: {e}")

    return []


def fetch_api(api_key: str, area_nm: str = "인천광역시", fact_manage_nm: str = None, retries: int = 3):
    # 인증키 이중 인코딩 방지
    decoded_key = urllib.parse.unquote(api_key)
    encoded_key = urllib.parse.quote(decoded_key, safe="")

    params = [
        f"serviceKey={encoded_key}",
        f"areaNm={urllib.parse.quote(area_nm)}",
        "type=json",
    ]
    if fact_manage_nm:
        params.append(f"factManageNm={urllib.parse.quote(fact_manage_nm)}")

    url = f"{ENDPOINT}?{'&'.join(params)}"
    ctx = ssl.create_default_context()

    for attempt in range(1, retries + 1):
        try:
            req = urllib.request.Request(
                url,
                headers={"User-Agent": "CleanSYS-TMS-Collector/1.0", "Accept": "application/json, application/xml"},
            )
            with urllib.request.urlopen(req, timeout=25, context=ctx) as resp:
                raw_bytes = resp.read()
                text = raw_bytes.decode("utf-8", errors="replace")
                items = parse_response_body(text)
                if items:
                    return items
                print(f"[INFO] 응답 수신되었으나 item 0건 (attempt {attempt}, fact={fact_manage_nm or 'ALL'})")
        except Exception as e:
            print(f"[WARN] API 호출 실패 (attempt {attempt}/{retries}, fact={fact_manage_nm or 'ALL'}): {e}")
        if attempt < retries:
            time.sleep(2 * attempt)
    return []


def load_json(path: str, default):
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"[WARN] 기존 JSON 로드 실패 ({path}): {e}")
    return default


def save_json(path: str, payload):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def make_empty_day(date_str: str):
    stacks = {}
    for st in TARGET_STACKS:
        stacks[st["id"]] = {
            "id": st["id"],
            "corpKey": st["corpKey"],
            "corpLabel": st["corpLabel"],
            "rawCorpName": st["corpLabel"],
            "stackCode": st["stackCode"],
            "stdNox": None,
            "slots": {},
        }
    return {
        "date": date_str,
        "updatedAtKst": now_kst().strftime("%Y-%m-%d %H:%M:%S"),
        "stacks": stacks,
    }


def main():
    api_key = os.environ.get("CLEANSYS_API_KEY", "").strip()
    if not api_key:
        print("[ERROR] 환경변수 CLEANSYS_API_KEY가 설정되어 있지 않습니다. GitHub Secrets를 확인하세요.")
        sys.exit(1)

    os.makedirs(DATA_DIR, exist_ok=True)
    run_ts = now_kst().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[START] CleanSYS 실측 수집 시작 ({run_ts} KST)")

    # 1) 인천광역시 전체 공식 파라미터 단일 호출
    all_items = fetch_api(api_key, area_nm="인천광역시")
    print(f"[INFO] 광역 호출 수신 건수: {len(all_items)}")

    # 2) 수신된 항목 중 매칭된 배출구 파악
    matched_ids = set()
    target_lookup = {(st["corpKey"], st["stackCode"]): st["id"] for st in TARGET_STACKS}

    for item in all_items:
        area = str(item.get("area_nm") or item.get("areaNm") or "")
        if area and "인천" not in area:
            continue
        ck = match_canonical_corp(item.get("fact_manage_nm") or item.get("factManageNm") or "")
        sn = parse_clean_stack_number(item.get("stack_code") or item.get("stackCode"))
        if (ck, sn) in target_lookup:
            matched_ids.add(target_lookup[(ck, sn)])

    # 3) 누락된 사업장이 있을 때만 정중하게 순차 보완 조회
    missing_corps = sorted({st["corpKey"] for st in TARGET_STACKS if st["id"] not in matched_ids})
    if missing_corps:
        print(f"[INFO] 누락 사업장 보완 조회 대상: {missing_corps}")
        for ck in missing_corps:
            for kw in FALLBACK_KEYWORDS.get(ck, [ck]):
                time.sleep(0.5)
                extra = fetch_api(api_key, area_nm="인천광역시", fact_manage_nm=kw, retries=2)
                if extra:
                    all_items.extend(extra)
                    print(f"[INFO] 보완 조회({kw}) 수신: {len(extra)}건")

    # 4) 날짜별 파일에 슬롯 병합 (동일 날짜/시각 슬롯만 정확히 갱신)
    day_cache = {}
    updated_count = 0
    matched_stack_ids = set()

    for item in all_items:
        area = str(item.get("area_nm") or item.get("areaNm") or "")
        if area and "인천" not in area:
            continue

        raw_corp = str(item.get("fact_manage_nm") or item.get("factManageNm") or "").strip()
        ck = match_canonical_corp(raw_corp)
        if not ck:
            continue

        sn = parse_clean_stack_number(item.get("stack_code") or item.get("stackCode"))
        stack_id = target_lookup.get((ck, sn))
        if not stack_id:
            continue

        date_str, slot_time = parse_mesure_dt(item.get("mesure_dt") or item.get("mesureDt"))
        if not date_str or not slot_time:
            continue

        nox_val, status_txt, raw_val_str = extract_strict_nox(item)
        if nox_val is None and not status_txt:
            continue

        std_nox = extract_strict_nox_std(item)

        if date_str not in day_cache:
            day_path = os.path.join(DATA_DIR, f"{date_str}.json")
            day_cache[date_str] = load_json(day_path, make_empty_day(date_str))
            # 신규 배출구 스키마 보정
            for st in TARGET_STACKS:
                if st["id"] not in day_cache[date_str]["stacks"]:
                    day_cache[date_str]["stacks"][st["id"]] = {
                        "id": st["id"],
                        "corpKey": st["corpKey"],
                        "corpLabel": st["corpLabel"],
                        "rawCorpName": st["corpLabel"],
                        "stackCode": st["stackCode"],
                        "stdNox": None,
                        "slots": {},
                    }

        stack_obj = day_cache[date_str]["stacks"][stack_id]
        if raw_corp:
            stack_obj["rawCorpName"] = raw_corp
        if std_nox is not None:
            stack_obj["stdNox"] = std_nox

        new_slot = {
            "nox": nox_val,
            "status": status_txt or "정상가동",
        }
        if raw_val_str and nox_val is None:
            new_slot["raw"] = raw_val_str

        if stack_obj["slots"].get(slot_time) != new_slot:
            stack_obj["slots"][slot_time] = new_slot
            # 시간순 정렬 유지
            stack_obj["slots"] = dict(sorted(stack_obj["slots"].items()))
            updated_count += 1

        matched_stack_ids.add(stack_id)

    # 5) 변경된 일별 파일 저장
    for date_str, day_data in day_cache.items():
        day_data["updatedAtKst"] = run_ts
        day_path = os.path.join(DATA_DIR, f"{date_str}.json")
        save_json(day_path, day_data)

    # 6) data/index.json 메타데이터 갱신 (저장된 모든 과거 날짜 목록 자동 인덱싱)
    available_dates = sorted(
        [
            fn[:-5]
            for fn in os.listdir(DATA_DIR)
            if re.fullmatch(r"\d{4}-\d{2}-\d{2}\.json", fn)
        ],
        reverse=True,
    )

    index_payload = {
        "lastRunKst": run_ts,
        "lastMatchedStacks": len(matched_stack_ids),
        "totalTargetStacks": len(TARGET_STACKS),
        "latestDate": available_dates[0] if available_dates else None,
        "availableDates": available_dates,
    }
    save_json(os.path.join(DATA_DIR, "index.json"), index_payload)

    print(
        f"[DONE] 매칭 배출구: {len(matched_stack_ids)}/{len(TARGET_STACKS)}, "
        f"신규/갱신 슬롯: {updated_count}건, 누적 일수: {len(available_dates)}일"
    )


if __name__ == "__main__":
    main()
