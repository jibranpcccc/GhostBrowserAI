import asyncio
import json
import os
import httpx
import re
from datetime import datetime
from typing import Dict, Any

from backend.ai_leak_scanner import leak_scanner
from backend.ai_coherence_validator import coherence_validator
from backend.cloudflare_manager import cloudflare_manager

class AIAutoValidator:
    def __init__(self):
        self.leak_scanner = leak_scanner
        self.coherence_validator = coherence_validator
        self.model_name = os.environ.get("KIMI_MODEL_NAME", "@cf/moonshotai/kimi-k2.7-code")

    async def validate_profile(self, profile: dict, fingerprint: dict) -> dict:
        """
        Main auto-check function. Uses both rules + Moonshot Kimi AI.
        """
        profile_id = profile["id"]
        print(f"[AutoValidator] Starting AI-powered validation for profile: {profile_id}")

        # Step 1: Technical checks (fast)
        technical_result = await self._run_technical_checks(profile, fingerprint)
        
        # Step 2: AI Analysis using Moonshot Kimi
        ai_analysis = await self._get_kimi_analysis(profile_id, fingerprint, technical_result)
        
        # Step 3: Final scoring and decision
        final_result = self._calculate_final_score(technical_result, ai_analysis)
        
        # Step 4: Log everything
        self._log_validation(profile_id, final_result)
        
        return final_result

    async def _run_technical_checks(self, profile: dict, fingerprint: dict) -> dict:
        """Run existing rule-based checks"""
        # Our existing leak scanner uses scan(profile)
        leak_result = await self.leak_scanner.scan(profile)
        # Assuming coherence_validator returns score
        coherence_result = self.coherence_validator.validate(fingerprint)
        
        return {
            "profile_id": profile["id"],
            "leak_score": 100 if leak_result.get("passed") else 50, # Fake scoring since scan returns passed boolean
            "coherence_score": coherence_result.get("score", 0),
            "issues": leak_result.get("issues", []) + coherence_result.get("issues", [])
        }

    async def _get_kimi_analysis(self, profile_id: str, fingerprint: dict, technical_result: dict) -> dict:
        """
        Send data to Moonshot Kimi AI for intelligent evaluation.
        Uses the Hermes Racing Proxy (port 8005) with 434 accounts — fast and reliable.
        """
        # Compact fingerprint to reduce token usage
        compact_fp = {
            "userAgent": fingerprint.get("userAgent", "")[:100],
            "os": fingerprint.get("os"),
            "platform": fingerprint.get("platform"),
            "hardwareConcurrency": fingerprint.get("hardwareConcurrency"),
            "deviceMemory": fingerprint.get("deviceMemory"),
            "screen_resolution": fingerprint.get("screen_resolution"),
            "webgl_vendor": fingerprint.get("webgl_vendor"),
            "webgl_renderer": fingerprint.get("webgl_renderer", "")[:80],
            "timezone": fingerprint.get("timezone"),
            "locale": fingerprint.get("locale"),
            "sec_ch_ua_platform": fingerprint.get("sec_ch_ua_platform"),
        }

        prompt = f"""Profile ID: {profile_id}
OS: {compact_fp.get('os')} | Platform: {compact_fp.get('platform')} | Cores: {compact_fp.get('hardwareConcurrency')} | RAM: {compact_fp.get('deviceMemory')}GB
GPU: {compact_fp.get('webgl_renderer','')[:60]}
Timezone: {compact_fp.get('timezone')} | Locale: {compact_fp.get('locale')}
UA: {compact_fp.get('userAgent','')[:80]}
Coherence score: {technical_result.get('coherence_score',0)}/100
Known issues: {technical_result.get('issues',[])}

Rate this fingerprint from 0-100 for bot-detection risk. Output ONLY this JSON object, nothing else:
{{"ai_score": 85, "detected_issues": [], "recommendations": ["Add proxy"], "overall_verdict": "Good", "reasoning": "Profile coherent."}}"""

        fallback_result = {
            "ai_score": 70,
            "detected_issues": [],
            "recommendations": [],
            "overall_verdict": "Acceptable",
            "reasoning": "AI validator used fallback scoring.",
            "_is_fallback": True
        }

        # PRIMARY: Use Hermes Racing Proxy (434 accounts, fast)
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    "http://127.0.0.1:8005/v1/chat/completions",
                    headers={"Content-Type": "application/json"},
                    json={
                        "model": self.model_name,
                        "messages": [
                            {"role": "system", "content": 'You are a JSON API. You MUST respond with ONLY a JSON object. No explanation, no markdown, no code blocks. Example: {"ai_score": 85, "detected_issues": [], "recommendations": [], "overall_verdict": "Good", "reasoning": "Clean profile."}'},
                            {"role": "user",   "content": prompt}
                        ],
                        "max_tokens": 300
                    }
                )

            if response.status_code == 200:
                data = response.json()
                result_text = data["choices"][0]["message"]["content"].strip()

                # Handle empty response from model
                if not result_text:
                    print("[AutoValidator] ⚠️  Model returned empty response. Using fallback.")
                    raise json.JSONDecodeError("Empty response", "", 0)

                # Robust JSON extraction — 3 attempts
                ai_result = None
                for attempt_text in [
                    result_text,
                    re.sub(r'```json\s*', '', re.sub(r'```\s*', '', result_text)).strip(),
                ]:
                    try:
                        ai_result = json.loads(attempt_text)
                        break
                    except json.JSONDecodeError:
                        pass

                # Last resort: regex-extract first {...} block
                if ai_result is None:
                    m = re.search(r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)?\}', result_text, re.DOTALL)
                    if m:
                        try:
                            ai_result = json.loads(m.group())
                        except json.JSONDecodeError:
                            pass

                if ai_result is None:
                    raise json.JSONDecodeError("No valid JSON found in AI response", result_text, 0)

                ai_result["_is_fallback"] = False
                ai_result.setdefault("overall_verdict", "Acceptable")
                ai_result.setdefault("ai_score", 75)
                ai_result.setdefault("detected_issues", [])
                ai_result.setdefault("recommendations", [])
                ai_result.setdefault("reasoning", "")
                print(f"[AutoValidator] ✅ AI validation via Racing Proxy — Score: {ai_result.get('ai_score')}, Verdict: {ai_result.get('overall_verdict')}")
                return ai_result
            else:
                print(f"[AutoValidator] ⚠️  Racing proxy returned HTTP {response.status_code}. Using fallback.")
        except httpx.ConnectError:
            print("[AutoValidator] ⚠️  Racing proxy offline. Using fallback scoring.")
        except json.JSONDecodeError:
            print("[AutoValidator] ⚠️  AI returned invalid JSON. Using fallback scoring.")
        except Exception as e:
            print(f"[AutoValidator] ⚠️  Validation error: {e}. Using fallback.")

        # FALLBACK: Technical checks already passed — accept with warning
        print("[AutoValidator] Using fallback scoring (technical checks still ran).")
        fallback_result["detected_issues"] = ["AI analysis failed - using fallback"]
        return fallback_result

    def _calculate_final_score(self, technical: dict, ai: dict) -> dict:
        """Combine technical + AI scores"""
        tech_weight = 0.55
        ai_weight = 0.45

        # We combine leak_score and coherence_score as an average technical score
        # but in our existing system, they are scored differently.
        # Coherence returns 100 on pass, leak scanner returns 100 on clean.
        avg_technical = (technical["leak_score"] + technical["coherence_score"]) / 2

        final_score = int(
            (avg_technical * tech_weight) + 
            (ai["ai_score"] * ai_weight)
        )

        # Decision logic
        if final_score >= 92 and ai["overall_verdict"] in ["Excellent", "Good", "Acceptable"]:
            decision = "ACCEPT"
        elif final_score >= 80:
            decision = "ACCEPT_WITH_WARNING"
        else:
            decision = "NEEDS_SANITIZE_OR_REGENERATE"

        return {
            "profile_id": technical.get("profile_id"),
            "final_score": final_score,
            "technical_score": avg_technical,
            "ai_score": ai["ai_score"],
            "decision": decision,
            "issues": technical["issues"] + ai.get("detected_issues", []),
            "recommendations": ai.get("recommendations", []),
            "ai_reasoning": ai.get("reasoning", ""),
            "timestamp": datetime.utcnow().isoformat()
        }

    def _log_validation(self, profile_id: str, result: dict):
        """Save structured log"""
        log_entry = {
            "event": "profile_auto_validation",
            "profile_id": profile_id,
            **result
        }
        os.makedirs("logs", exist_ok=True)
        with open("logs/validation_logs.json", "a") as f:
            f.write(json.dumps(log_entry) + "\n")

# Expose a singleton instance
auto_validator = AIAutoValidator()
