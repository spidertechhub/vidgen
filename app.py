import os
import uuid
import random
import math
from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
import yt_dlp
from supabase import create_client, Client

# --- PIL COMPATIBILITY PATCH ---
from PIL import Image
if not hasattr(Image, 'ANTIALIAS'):
    Image.ANTIALIAS = Image.LANCZOS

from moviepy.editor import ImageClip, AudioFileClip, ColorClip, CompositeVideoClip, concatenate_videoclips

app = Flask(__name__)
CORS(app) 

# --- SUPABASE CONFIG ---
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

if SUPABASE_URL and SUPABASE_KEY:
    supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
else:
    print("⚠️ WARNING: Supabase credentials missing!")

UPLOAD_DIR = 'uploads'
os.makedirs(UPLOAD_DIR, exist_ok=True)

@app.route('/')
def home():
    return send_file('index.html')

@app.route('/api/history', methods=['GET'])
def get_history():
    """Scans the Supabase 'videos' bucket directly."""
    if not SUPABASE_URL:
        return jsonify([])
        
    try:
        res = supabase.storage.from_('videos').list()
        files_data = []
        for file in res:
            if file['name'].endswith('.mp4'):
                public_url = supabase.storage.from_('videos').get_public_url(file['name'])
                # Supabase forces downloads if you append ?download=
                download_url = f"{public_url}?download={file['name']}"
                files_data.append({
                    "filename": file['name'],
                    "created_at": file['created_at'],
                    "preview": public_url,
                    "download": download_url
                })
        # Sort newest first
        files_data.sort(key=lambda x: x['created_at'], reverse=True)
        return jsonify(files_data)
    except Exception as e:
        print(f"Supabase History Error: {e}")
        return jsonify([])

def fit_and_fill(clip, target_size=(720, 1280)):
    target_ratio = target_size[0] / target_size[1]
    clip_ratio = clip.w / clip.h
    
    if clip_ratio > target_ratio:
        resized = clip.resize(height=target_size[1])
        x_center = resized.w / 2
        return resized.crop(x1=x_center - target_size[0]/2, y1=0, 
                            x2=x_center + target_size[0]/2, y2=target_size[1])
    else:
        resized = clip.resize(width=target_size[0])
        y_center = resized.h / 2
        return resized.crop(x1=0, y1=y_center - target_size[1]/2, 
                            x2=target_size[0], y2=y_center + target_size[1]/2)

def apply_animation(clip, target_size=(720, 1280), duration=4.0):
    bg = ColorClip(size=target_size, color=(0,0,0)).set_duration(duration)
    effects = ['zoom_in', 'zoom_out']
    effect = random.choice(effects)
    zoom_rate = 0.15 / duration
    
    try:
        base = fit_and_fill(clip, target_size)
        if effect == 'zoom_in':
            anim = base.resize(lambda t: 1 + (zoom_rate * t)).set_position('center')
        else:
            anim = base.resize(lambda t: 1.15 - (zoom_rate * t)).set_position('center')
        return CompositeVideoClip([bg, anim]).set_duration(duration)
    except Exception as e:
        base = fit_and_fill(clip, target_size)
        return CompositeVideoClip([bg, base.set_position('center')]).set_duration(duration)

@app.route('/api/render', methods=['POST'])
def render_video():
    if not SUPABASE_URL:
         return jsonify({"error": "Server missing Supabase keys."}), 500

    fb_url = request.form.get('url')
    files = request.files.getlist('images')
    
    if not fb_url or not files:
        return jsonify({"error": "Missing URL or images"}), 400
        
    session_id = uuid.uuid4().hex
    audio_path = os.path.join(UPLOAD_DIR, f"{session_id}.mp3")
    output_filename = f"video_{session_id}.mp4"
    output_path = os.path.join(UPLOAD_DIR, output_filename)
    
    # 1. DOWNLOAD AUDIO
    ydl_opts = {
        'format': 'bestaudio/best',
        'outtmpl': audio_path.replace('.mp3', '.%(ext)s'),
        'postprocessors': [{'key': 'FFmpegExtractAudio', 'preferredcodec': 'mp3'}],
        'quiet': True
    }
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([fb_url])
        audio = AudioFileClip(audio_path)
        total_audio_duration = audio.duration if (audio.duration and audio.duration > 0) else 15.0
    except Exception as e:
        return jsonify({"error": f"Audio extraction failed: {str(e)}"}), 500

    saved_images = []
    try:
        for idx, file in enumerate(files):
            img_path = os.path.join(UPLOAD_DIR, f"{session_id}_{idx}_{file.filename}")
            file.save(img_path)
            saved_images.append(img_path)
            
        # 2. HARDCODED TIMELINE
        if len(saved_images) == 1:
            clip = ImageClip(saved_images[0]).set_duration(total_audio_duration)
            base = fit_and_fill(clip, (720, 1280))
            bg = ColorClip(size=(720, 1280), color=(0,0,0)).set_duration(total_audio_duration)
            video = CompositeVideoClip([bg, base.set_position('center')]).set_duration(total_audio_duration)
        else:
            clips = []
            current_time = 0.0
            idx = 0
            while current_time < total_audio_duration:
                current_img_path = saved_images[idx % len(saved_images)]
                clip_duration = random.uniform(2.5, 5.0)
                
                time_left = total_audio_duration - current_time
                if time_left < clip_duration:
                    clip_duration = time_left + 1.0 
                
                clip = ImageClip(current_img_path).set_duration(clip_duration)
                anim_clip = apply_animation(clip, duration=clip_duration)
                
                if idx > 0:
                    anim_clip = anim_clip.crossfadein(1.0)
                    
                clips.append(anim_clip)
                current_time += (clip_duration - 1.0)
                idx += 1
                
            video = concatenate_videoclips(clips, padding=-1.0, method="compose").set_duration(total_audio_duration)
        
        # 3. RENDER MP4
        video = video.set_audio(audio)
        cpu_cores = os.cpu_count() or 4
        video.write_videofile(output_path, fps=24, codec="libx264", audio_codec="aac", threads=cpu_cores, preset="ultrafast")
        
        # 4. UPLOAD TO SUPABASE
        with open(output_path, "rb") as f:
            supabase.storage.from_("videos").upload(
                path=output_filename, 
                file=f, 
                file_options={"content-type": "video/mp4"}
            )
            
        public_url = supabase.storage.from_('videos').get_public_url(output_filename)
        download_url = f"{public_url}?download={output_filename}"
        
        return jsonify({"video_url": public_url, "download_url": download_url})
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"error": f"Video rendering failed: {str(e)}"}), 500
        
    finally:
        # 5. AGGRESSIVE CLEANUP TO SAVE CLOUD DISK SPACE
        for img in saved_images:
            if os.path.exists(img): os.remove(img)
        if os.path.exists(audio_path):
            try: os.remove(audio_path)
            except: pass
        if os.path.exists(output_path):
            try: os.remove(output_path)
            except: pass

if __name__ == '__main__':
    # Uses Render's injected PORT environment variable
    port = int(os.environ.get("PORT", 5000))
    print(f"🚀 Cloud Engine Starting on Port {port}")
    app.run(host='0.0.0.0', port=port)
