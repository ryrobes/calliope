package ai.rvbbit.calliope.mobile;

import android.os.Bundle;
import android.view.*;
import android.content.res.Configuration;
import android.content.Intent;
import android.content.ActivityNotFoundException;
import android.net.Uri;
import android.widget.Toast;
import dev.gpui.mobile.GpuiInputActivity;

/** Java owns lifecycle and the IME; GPUI owns the entire visible app surface. */
public final class CalliopeActivity extends GpuiInputActivity implements SurfaceHolder.Callback {
    private SurfaceView view;
    private AudioBridge audio;
    private String pendingLink;
    public synchronized String takeLink() { String value = pendingLink; pendingLink = null; return value; }
    private synchronized void receiveLink(Intent intent) {
        Uri uri = intent == null ? null : intent.getData();
        if (uri != null && "calliope".equals(uri.getScheme()) && uri.toString().length() <= 4096) pendingLink = uri.toString();
    }
    @Override protected void onNewIntent(Intent intent) { super.onNewIntent(intent); setIntent(intent); receiveLink(intent); }
    public String audioCommand(String request) { return audio.command(request); }
    public void capturePhoto(String path) { Attachments.capture(this,path); }
    public void chooseAttachment() { Attachments.choose(this); }
    public void openAttachment(String path,String mime) { Attachments.open(this,path,mime); }
    native void attachmentPicked(String result);
    @Override protected void onActivityResult(int request,int result,Intent data) {
        super.onActivityResult(request,result,data);
        if(request==Attachments.CAMERA) Attachments.cameraResult(this,result);
        if(request==Attachments.PICK) Attachments.result(this,result,data);
    }
    public String loadConnection() throws Exception { return ConnectionStore.load(this); }
    public void saveConnection(String value) throws Exception { ConnectionStore.save(this, value); }
    public String trustedCertificates() throws Exception { return CertificateTrust.certificates(); }
    // Android dispatches Back to the IME first. Only app-level Back reaches here.
    @Override public void onBackPressed() { back(); }
    public void backgroundApp() { runOnUiThread(() -> moveTaskToBack(true)); }
    public void openBrowser(String url) {
        runOnUiThread(() -> {
            Uri uri = Uri.parse(url);
            if (!"https".equals(uri.getScheme()) && !"http".equals(uri.getScheme())) return;
            try {
                // Browser owns its own login session; never put our cookie in an Intent.
                startActivity(new Intent(Intent.ACTION_VIEW, uri).addCategory(Intent.CATEGORY_BROWSABLE));
            } catch (ActivityNotFoundException error) {
                Toast.makeText(this, "No browser is available", Toast.LENGTH_LONG).show();
            }
        });
    }
    @Override public void onCreate(Bundle state) {
        super.onCreate(state);
        receiveLink(getIntent());
        audio = new AudioBridge(this);
        view = new SurfaceView(this);
        view.setFocusableInTouchMode(true);
        view.getHolder().addCallback(this);
        view.setOnTouchListener((v, e) -> {
            int count = e.getPointerCount();
            int[] ids = new int[count]; float[] xs = new float[count]; float[] ys = new float[count];
            for (int i = 0; i < count; i++) { ids[i] = e.getPointerId(i); xs[i] = e.getX(i); ys[i] = e.getY(i); }
            return motion(e.getActionMasked(), e.getActionIndex(), ids, xs, ys);
        });
        create(this, getFilesDir().getAbsolutePath());
        setContentView(view);
        // Best effort: notifications must never keep the app from opening.
        try { WaitingCheck.schedule(this); } catch (RuntimeException ignored) { }
        askForNotifications();
    }
    /** Once signed in, ask (once) to show "Callie is waiting" notifications. */
    private void askForNotifications() {
        if (android.os.Build.VERSION.SDK_INT < 33) return;
        if (checkSelfPermission("android.permission.POST_NOTIFICATIONS") == android.content.pm.PackageManager.PERMISSION_GRANTED) return;
        android.content.SharedPreferences prefs = getSharedPreferences("callie-waiting", MODE_PRIVATE);
        if (prefs.getBoolean("asked", false)) return;
        try {
            if (!new org.json.JSONObject(ConnectionStore.load(this)).optString("session_cookie").isEmpty()) {
                prefs.edit().putBoolean("asked", true).apply();
                requestPermissions(new String[] {"android.permission.POST_NOTIFICATIONS"}, 4202);
            }
        } catch (Exception ignored) { /* not signed in yet: ask on a later launch */ }
    }
    @Override protected void onResume() { super.onResume(); if (audio != null) audio.foreground(true); lifecycle(1); }
    @Override protected void onPause() { if (audio != null) audio.foreground(false); lifecycle(0); super.onPause(); }
    @Override public void onConfigurationChanged(Configuration config) { super.onConfigurationChanged(config); lifecycle(2); }
    @Override public void surfaceCreated(SurfaceHolder holder) { surface(holder.getSurface(), getResources().getDisplayMetrics().density, false); }
    @Override public void surfaceChanged(SurfaceHolder holder, int format, int width, int height) { surfaceCreated(holder); }
    @Override public void surfaceDestroyed(SurfaceHolder holder) { surface(holder.getSurface(), 1f, true); }
    @Override public boolean dispatchKeyEvent(KeyEvent event) {
        int code = event.getKeyCode();
        if (code == KeyEvent.KEYCODE_BACK || code == KeyEvent.KEYCODE_VOLUME_UP || code == KeyEvent.KEYCODE_VOLUME_DOWN) return super.dispatchKeyEvent(event);
        if (event.isCtrlPressed() || code == KeyEvent.KEYCODE_TAB || code == KeyEvent.KEYCODE_ESCAPE) {
            key(code, event.getAction(), event.getMetaState()); return true;
        }
        return super.dispatchKeyEvent(event);
    }
    private native void create(Object activity, String files);
    private native void surface(Surface surface, float scale, boolean destroyed);
    private native void lifecycle(int event);
    private native boolean motion(int action, int index, int[] ids, float[] xs, float[] ys);
    private native void key(int code, int action, int meta);
    private native void back();
}
