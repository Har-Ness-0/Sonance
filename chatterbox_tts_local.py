"""Chatterbox TTS API - Local GPU server."""

import io
import os
import re
import tempfile
from pathlib import Path

import boto3
import torch
import torchaudio as ta
from botocore.config import Config
from chatterbox.tts_turbo import ChatterboxTurboTTS
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Security
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.security import APIKeyHeader
from pydantic import BaseModel, Field


# ============================================================
# Environment
# ============================================================

load_dotenv()

hf_token = os.getenv("HF_ACCESS_TOKEN")

if not hf_token:
    raise RuntimeError("HF_ACCESS_TOKEN is not set")

os.environ["HF_TOKEN"] = hf_token

print("Hugging Face token loaded successfully")


# ============================================================
# Configuration
# ============================================================

R2_BUCKET_NAME = os.environ["R2_BUCKET_NAME"]
R2_ACCOUNT_ID = os.environ["R2_ACCOUNT_ID"]

R2_ENDPOINT_URL = (
    f"https://{R2_ACCOUNT_ID}.r2.cloudflarestorage.com"
)


# ============================================================
# Cloudflare R2
# ============================================================

r2_client = boto3.client(
    "s3",
    endpoint_url=R2_ENDPOINT_URL,
    aws_access_key_id=os.environ["R2_ACCESS_KEY_ID"],
    aws_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"],
    region_name="auto",
    config=Config(signature_version="s3v4"),
)


# ============================================================
# API key authentication
# ============================================================

api_key_scheme = APIKeyHeader(
    name="x-api-key",
    scheme_name="ApiKeyAuth",
    auto_error=False,
)


def verify_api_key(
    x_api_key: str | None = Security(api_key_scheme),
):
    expected = os.environ.get(
        "CHATTERBOX_API_KEY",
        "",
    )

    if not expected or x_api_key != expected:
        raise HTTPException(
            status_code=403,
            detail="Invalid API key",
        )

    return x_api_key


# ============================================================
# Request model
# ============================================================

class TTSRequest(BaseModel):

    prompt: str = Field(
        ...,
        min_length=1,
        max_length=5000,
    )

    voice_key: str = Field(
        ...,
        min_length=1,
        max_length=300,
    )

    temperature: float = Field(
        default=0.8,
        ge=0.0,
        le=2.0,
    )

    top_p: float = Field(
        default=0.95,
        ge=0.0,
        le=1.0,
    )

    top_k: int = Field(
        default=1000,
        ge=1,
        le=10000,
    )

    repetition_penalty: float = Field(
        default=1.2,
        ge=1.0,
        le=2.0,
    )

    norm_loudness: bool = Field(
        default=True,
    )


# ============================================================
# Chatterbox
# ============================================================

class Chatterbox:

    def __init__(self):

        print(
            "Loading Chatterbox model on CUDA..."
        )

        self.model = ChatterboxTurboTTS.from_pretrained(
            device="cuda"
        )

        print(
            "Chatterbox model loaded successfully."
        )

    def download_voice(
        self,
        voice_key: str,
    ) -> str:
        """Download a voice file from Cloudflare R2."""

        voice_key = voice_key.lstrip("/")

        temp_dir = tempfile.mkdtemp(
            prefix="chatterbox_voice_"
        )

        filename = os.path.basename(
            voice_key
        )

        local_path = os.path.join(
            temp_dir,
            filename,
        )

        try:

            r2_client.download_file(
                R2_BUCKET_NAME,
                voice_key,
                local_path,
            )

        except Exception as e:

            raise HTTPException(
                status_code=400,
                detail=(
                    f"Voice not found at "
                    f"'{voice_key}': {e}"
                ),
            )

        return local_path

    def generate(
        self,
        prompt: str,
        audio_prompt_path: str,
        temperature: float = 0.8,
        top_p: float = 0.95,
        top_k: int = 1000,
        repetition_penalty: float = 1.2,
        norm_loudness: bool = True,
    ):

        # Generate audio directly as a tensor.
        #
        # IMPORTANT:
        # We do NOT save each chunk to WAV here.
        # This avoids the "unknown format: 3" error.

        wav = self.model.generate(
            prompt,
            audio_prompt_path=audio_prompt_path,
            temperature=temperature,
            top_p=top_p,
            top_k=top_k,
            repetition_penalty=repetition_penalty,
            norm_loudness=norm_loudness,
        )

        return wav


# ============================================================
# Load model once when server starts
# ============================================================

chatterbox = Chatterbox()


# ============================================================
# FastAPI
# ============================================================

app = FastAPI(
    title="Chatterbox TTS API",
    description="Text-to-speech with voice cloning",
    docs_url="/docs",
    dependencies=[
        Depends(verify_api_key)
    ],
)


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============================================================
# Text Chunking
# ============================================================

def split_text_into_chunks(
    text: str,
    max_chars: int = 350,
):
    """
    Split text into sentence-aware chunks.

    Chatterbox receives shorter chunks to reduce
    long-context generation drift.
    """

    text = text.strip()

    if not text:
        return []

    sentences = re.split(
        r"(?<=[.!?])\s+",
        text,
    )

    chunks = []
    current = ""

    for sentence in sentences:

        sentence = sentence.strip()

        if not sentence:
            continue

        # ----------------------------------------------------
        # Sentence fits into current chunk
        # ----------------------------------------------------

        if (
            len(current)
            + len(sentence)
            + 1
            <= max_chars
        ):

            current = (
                f"{current} {sentence}"
                .strip()
            )

        else:

            # Save current chunk
            if current:
                chunks.append(current)

            # ------------------------------------------------
            # Handle sentence longer than max_chars
            # ------------------------------------------------

            if len(sentence) > max_chars:

                words = sentence.split()

                current = ""

                for word in words:

                    if (
                        len(current)
                        + len(word)
                        + 1
                        <= max_chars
                    ):

                        current = (
                            f"{current} {word}"
                            .strip()
                        )

                    else:

                        if current:
                            chunks.append(
                                current
                            )

                        current = word

            else:

                current = sentence

    # Save final chunk
    if current:
        chunks.append(current)

    return chunks


# ============================================================
# Generate
# ============================================================

@app.post(
    "/generate",
    responses={
        200: {
            "content": {
                "audio/wav": {}
            }
        }
    },
)
def generate_speech(
    request: TTSRequest,
):

    print(
        f"Generating speech: "
        f"{request.prompt[:80]}..."
    )

    voice_path = chatterbox.download_voice(
        request.voice_key
    )

    try:

        # ----------------------------------------------------
        # Split text
        # ----------------------------------------------------

        chunks = split_text_into_chunks(
            request.prompt,
            max_chars=350,
        )

        print(
            f"Text split into "
            f"{len(chunks)} chunk(s)"
        )

        for i, chunk in enumerate(
            chunks,
            start=1,
        ):

            print(
                f"Chunk {i}/{len(chunks)}: "
                f"{chunk[:100]}..."
            )

        # ----------------------------------------------------
        # Generate each chunk
        # ----------------------------------------------------

        audio_segments = []

        for i, chunk in enumerate(
            chunks,
            start=1,
        ):

            print(
                f"Generating chunk "
                f"{i}/{len(chunks)}..."
            )

            wav = chatterbox.generate(
                prompt=chunk,
                audio_prompt_path=voice_path,
                temperature=request.temperature,
                top_p=request.top_p,
                top_k=request.top_k,
                repetition_penalty=request.repetition_penalty,
                norm_loudness=request.norm_loudness,
            )

            # Make sure the tensor is on CPU.
            #
            # This also prevents unnecessary GPU memory
            # from being held between chunks.

            wav = wav.detach().cpu()

            audio_segments.append(wav)

            print(
                f"Chunk {i} generated successfully."
            )

        # ----------------------------------------------------
        # Combine audio tensors
        # ----------------------------------------------------

        print(
            "Combining audio chunks..."
        )

        if not audio_segments:
            raise RuntimeError(
                "No audio was generated."
            )

        # Chatterbox normally returns:
        #
        # [channels, samples]
        #
        # Concatenate along the sample dimension.

        combined_wav = torch.cat(
            audio_segments,
            dim=1,
        )

        print(
            f"Combined audio shape: "
            f"{tuple(combined_wav.shape)}"
        )

        # ----------------------------------------------------
        # Save ONE final WAV
        # ----------------------------------------------------

        output = io.BytesIO()

        ta.save(
            output,
            combined_wav,
            chatterbox.model.sr,
            format="wav",
        )

        output.seek(0)

        print(
            f"Successfully generated "
            f"{len(chunks)} chunk(s)"
        )

        return StreamingResponse(
            output,
            media_type="audio/wav",
            headers={
                "Content-Disposition":
                    'inline; filename="output.wav"'
            },
        )

    except Exception as e:

        print(
            f"Generation failed: {e}"
        )

        raise HTTPException(
            status_code=500,
            detail=(
                f"Failed to generate audio: {e}"
            ),
        )

    finally:

        # ----------------------------------------------------
        # Clean up temporary voice file
        # ----------------------------------------------------

        try:

            Path(
                voice_path
            ).unlink(
                missing_ok=True
            )

            Path(
                voice_path
            ).parent.rmdir()

        except Exception:
            pass


# ============================================================
# Health check
# ============================================================

@app.get("/health")
def health():

    return {
        "status": "ok",
        "gpu": "cuda",
        "model": "chatterbox-turbo",
    }