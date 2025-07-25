from fastapi import FastAPI, UploadFile, File
from fastapi.responses import JSONResponse
import librosa
import numpy as np
import torch
import torchaudio
from torchaudio.pipelines import WAV2VEC2_BASE, HUBERT_BASE
import uvicorn
import tempfile

app = FastAPI()

# Load models once at startup
wav2vec_model = WAV2VEC2_BASE.get_model()
hubert_model = HUBERT_BASE.get_model()

# Level classification helper
def classify_cefr_level(value, thresholds):
    if value < thresholds[0]:
        return "A1"
    elif value < thresholds[1]:
        return "A2"
    elif value < thresholds[2]:
        return "B1"
    elif value < thresholds[3]:
        return "B2"
    elif value < thresholds[4]:
        return "C1"
    else:
        return "C2"

# Feedback
def get_explanation(level):
    return {
        "A1": "Very limited fluency, slow and hesitant delivery.",
        "A2": "Basic fluency with frequent pauses and low variation.",
        "B1": "Fair fluency with simple rhythm and moderate control.",
        "B2": "Good fluency and control; some complexity observed.",
        "C1": "Fluent and confident speech with nuanced intonation.",
        "C2": "Highly fluent, articulate, and natural sounding delivery."
    }[level]

# Extract features for rule-based
def extract_basic_features(y, sr):
    mfcc = librosa.feature.mfcc(y=y, sr=sr, n_mfcc=13)
    tempo, _ = librosa.beat.beat_track(y=y, sr=sr)
    return {
        "mfcc_std": float(np.std(mfcc)),
        "tempo": float(tempo),
    }

def estimate_level_basic(features):
    tempo = features["tempo"]
    mfcc_std = features["mfcc_std"]
    avg_score = (tempo + mfcc_std * 10) / 2
    return classify_cefr_level(avg_score, [70, 85, 100, 115, 130])

# Deep embedding feature extraction
def extract_deep_features(waveform, sr, model):
    if sr != 16000:
        waveform = torchaudio.functional.resample(waveform, sr, 16000)
    with torch.inference_mode():
        features = model(waveform).extractor_features
    return features.mean(dim=1).squeeze().numpy()

def estimate_level_embedding(embedding):
    energy = np.linalg.norm(embedding)
    return classify_cefr_level(energy, [85, 100, 115, 130, 145])

@app.post("/evaluate")
async def evaluate_audio(file: UploadFile = File(...)):
    with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as tmp:
        tmp.write(await file.read())
        tmp_path = tmp.name

    y, sr = librosa.load(tmp_path)
    waveform, sr_torch = torchaudio.load(tmp_path)

    # Rule-based
    basic_features = extract_basic_features(y, sr)
    level_basic = estimate_level_basic(basic_features)

    # Wav2Vec2
    emb_w2v = extract_deep_features(waveform, sr_torch, wav2vec_model)
    level_w2v = estimate_level_embedding(emb_w2v)

    # HuBERT
    emb_hubert = extract_deep_features(waveform, sr_torch, hubert_model)
    level_hubert = estimate_level_embedding(emb_hubert)

    result = {
        "basic": {"level": level_basic, "explanation": get_explanation(level_basic)},
        "wav2vec2": {"level": level_w2v, "explanation": get_explanation(level_w2v)},
        "hubert": {"level": level_hubert, "explanation": get_explanation(level_hubert)},
    }

    return JSONResponse(content=result)

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)