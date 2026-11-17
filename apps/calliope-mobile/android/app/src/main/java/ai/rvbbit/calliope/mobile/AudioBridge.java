package ai.rvbbit.calliope.mobile;

import android.Manifest;
import android.app.Activity;
import android.content.pm.PackageManager;
import android.media.*;
import android.os.SystemClock;
import android.util.Base64;
import org.json.JSONObject;

/** Device audio only. Transcription and synthesis use the normal Calliope server. */
final class AudioBridge {
    private final Activity activity;
    private final AudioManager manager;
    private long activeId = -1;
    private AudioRecord recorder;
    private AudioTrack player;
    private AudioFocusRequest focus;
    private long samplesWritten;
    private boolean foreground;

    AudioBridge(Activity activity) {
        this.activity = activity;
        manager = (AudioManager) activity.getSystemService(Activity.AUDIO_SERVICE);
    }

    synchronized void closeAll() { close(activeId); }
    synchronized void foreground(boolean value) {
        foreground = value;
        if (!value) closeAll();
    }
    private void requireForeground() {
        if (!foreground) throw new IllegalStateException("Return to Calliope to use voice.");
    }
    private synchronized void close(long id) {
        if (id != activeId) return;
        activeId = -1;
        if (recorder != null) {
            try { recorder.stop(); } catch (Exception ignored) {}
            recorder.release(); recorder = null;
        }
        if (player != null) {
            try { player.pause(); player.flush(); } catch (Exception ignored) {}
            player.release(); player = null;
        }
        if (focus != null) { manager.abandonAudioFocusRequest(focus); focus = null; }
    }

    String command(String raw) {
        try {
            JSONObject request = new JSONObject(raw);
            String op = request.getString("op");
            long id = request.getLong("id");
            JSONObject result = new JSONObject().put("ok", true);
            switch (op) {
                case "permission": {
                    synchronized (this) { requireForeground(); }
                    if (activity.checkSelfPermission(Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) {
                        activity.runOnUiThread(() -> activity.requestPermissions(new String[]{Manifest.permission.RECORD_AUDIO}, 803));
                        throw new IllegalStateException("Allow microphone access, then tap Dictate again. If access was denied, enable Microphone in Android app settings.");
                    }
                    break;
                }
                case "status": {
                    synchronized (this) {
                        return result.put("foreground", foreground)
                            .put("recording", recorder != null && recorder.getRecordingState() == AudioRecord.RECORDSTATE_RECORDING)
                            .put("playing", player != null && player.getPlayState() == AudioTrack.PLAYSTATE_PLAYING)
                            .put("focus", focus != null).put("session", activeId).toString();
                    }
                }
                case "record": {
                    synchronized (this) { requireForeground(); }
                    if (activity.checkSelfPermission(Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) {
                        activity.runOnUiThread(() -> activity.requestPermissions(new String[]{Manifest.permission.RECORD_AUDIO}, 803));
                        throw new IllegalStateException("Allow microphone access, then tap Dictate again.");
                    }
                    synchronized (this) {
                        requireForeground();
                        closeAll(); activeId = id;
                        int minimum = AudioRecord.getMinBufferSize(16000, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT);
                        if (minimum <= 0) throw new IllegalStateException("Microphone format unavailable");
                        recorder = new AudioRecord(MediaRecorder.AudioSource.VOICE_RECOGNITION, 16000,
                            AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT, Math.max(minimum * 2, 6400));
                        if (recorder.getState() != AudioRecord.STATE_INITIALIZED) { close(id); throw new IllegalStateException("Microphone unavailable"); }
                        recorder.startRecording();
                    }
                    break;
                }
                case "read": {
                    AudioRecord source;
                    synchronized (this) { source = activeId == id ? recorder : null; }
                    if (source == null) return result.put("data", "").toString();
                    byte[] bytes = new byte[3200];
                    int count;
                    try { count = source.read(bytes, 0, bytes.length, AudioRecord.READ_BLOCKING); }
                    catch (IllegalStateException stopped) {
                        synchronized (this) { if (activeId != id) return result.put("data", "").toString(); }
                        throw stopped;
                    }
                    synchronized (this) { if (activeId != id) count = 0; }
                    if (count < 0) throw new IllegalStateException("Microphone read failed: " + count);
                    result.put("data", Base64.encodeToString(bytes, 0, count, Base64.NO_WRAP));
                    break;
                }
                case "play": {
                    int rate = request.getInt("rate");
                    if (rate < 8000 || rate > 96000) throw new IllegalArgumentException("Invalid playback sample rate");
                    synchronized (this) {
                        requireForeground();
                        closeAll(); activeId = id;
                        AudioAttributes attributes = new AudioAttributes.Builder().setUsage(AudioAttributes.USAGE_ASSISTANT).setContentType(AudioAttributes.CONTENT_TYPE_SPEECH).build();
                        focus = new AudioFocusRequest.Builder(AudioManager.AUDIOFOCUS_GAIN_TRANSIENT_MAY_DUCK).setAudioAttributes(attributes)
                            .setOnAudioFocusChangeListener(change -> { if (change < 0) close(id); }).build();
                        if (manager.requestAudioFocus(focus) != AudioManager.AUDIOFOCUS_REQUEST_GRANTED) { close(id); throw new IllegalStateException("Audio is in use by another app"); }
                        int minimum = AudioTrack.getMinBufferSize(rate, AudioFormat.CHANNEL_OUT_MONO, AudioFormat.ENCODING_PCM_16BIT);
                        if (minimum <= 0) { close(id); throw new IllegalStateException("Speaker format unavailable"); }
                        player = new AudioTrack.Builder().setAudioAttributes(attributes)
                            .setAudioFormat(new AudioFormat.Builder().setSampleRate(rate).setChannelMask(AudioFormat.CHANNEL_OUT_MONO).setEncoding(AudioFormat.ENCODING_PCM_16BIT).build())
                            .setTransferMode(AudioTrack.MODE_STREAM).setBufferSizeInBytes(Math.max(minimum * 2, rate / 2)).build();
                        samplesWritten = 0; player.play();
                    }
                    break;
                }
                case "write": {
                    AudioTrack sink;
                    synchronized (this) { sink = activeId == id ? player : null; }
                    if (sink == null) throw new IllegalStateException("Playback stopped");
                    byte[] bytes = Base64.decode(request.getString("data"), Base64.DEFAULT);
                    if (bytes.length > 131072 || bytes.length % 2 != 0) throw new IllegalArgumentException("Invalid audio frame");
                    int offset = 0;
                    while (offset < bytes.length) {
                        int written = sink.write(bytes, offset, bytes.length - offset, AudioTrack.WRITE_BLOCKING);
                        if (written <= 0) throw new IllegalStateException("Speaker write failed");
                        offset += written;
                    }
                    synchronized (this) { if (activeId == id) samplesWritten += bytes.length / 2; }
                    break;
                }
                case "finish": {
                    long deadline = SystemClock.elapsedRealtime() + 5000;
                    while (SystemClock.elapsedRealtime() < deadline) {
                        synchronized (this) {
                            if (activeId != id || player == null) throw new IllegalStateException("Playback stopped");
                            if (Integer.toUnsignedLong(player.getPlaybackHeadPosition()) >= samplesWritten) { close(id); return result.toString(); }
                        }
                        SystemClock.sleep(20);
                    }
                    close(id); throw new IllegalStateException("Speaker did not finish playback");
                }
                case "stop": close(id); break;
                default: throw new IllegalArgumentException("Unknown audio operation");
            }
            return result.toString();
        } catch (Exception error) {
            try { return new JSONObject().put("error", error.getMessage() == null ? "Audio unavailable" : error.getMessage()).toString(); }
            catch (Exception impossible) { return "{\"error\":\"Audio unavailable\"}"; }
        }
    }
}
