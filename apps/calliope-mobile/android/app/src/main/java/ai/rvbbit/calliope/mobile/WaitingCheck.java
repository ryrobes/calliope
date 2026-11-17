package ai.rvbbit.calliope.mobile;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.job.JobInfo;
import android.app.job.JobParameters;
import android.app.job.JobScheduler;
import android.app.job.JobService;
import android.content.ComponentName;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.net.Uri;
import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.time.Instant;
import java.util.Collections;
import java.util.HashSet;
import java.util.Set;
import org.json.JSONArray;
import org.json.JSONObject;

/**
 * "Callie is waiting for you" without a push service: a periodic background
 * check of /api/calliope/awaiting (Android runs it at most every ~15 minutes)
 * raises a local notification for each new question, approval or decision.
 * Self-hosted installs need no Firebase project or relay.
 */
public final class WaitingCheck extends JobService {
    static final int JOB_ID = 4201;
    static final String CHANNEL = "callie-waiting";
    private static final long FRESH_MS = 24L * 60 * 60 * 1000;

    static void schedule(Context context) {
        channel(context);
        JobScheduler scheduler = context.getSystemService(JobScheduler.class);
        if (scheduler == null || scheduler.getPendingJob(JOB_ID) != null) return;
        scheduler.schedule(new JobInfo.Builder(JOB_ID, new ComponentName(context, WaitingCheck.class))
            .setRequiredNetworkType(JobInfo.NETWORK_TYPE_ANY)
            .setPeriodic(15 * 60 * 1000L)
            .setPersisted(true)
            .build());
    }

    private static void channel(Context context) {
        NotificationManager manager = context.getSystemService(NotificationManager.class);
        if (manager == null) return;
        NotificationChannel channel = new NotificationChannel(CHANNEL, "Callie is waiting", NotificationManager.IMPORTANCE_DEFAULT);
        channel.setDescription("Questions, approvals and decisions Callie needs from you");
        manager.createNotificationChannel(channel);
    }

    @Override public boolean onStartJob(JobParameters params) {
        new Thread(() -> {
            try { check(this); } catch (Exception ignored) { /* the next run tries again */ }
            finally { jobFinished(params, false); }
        }, "callie-waiting").start();
        return true;
    }

    @Override public boolean onStopJob(JobParameters params) { return true; }

    static void check(Context context) throws Exception {
        JSONObject connection = new JSONObject(ConnectionStore.load(context));
        String base = connection.optString("base_url"), cookie = connection.optString("session_cookie");
        if (base.isEmpty() || cookie.isEmpty()) return;
        URL url = new URL(new URL(base), "/api/calliope/awaiting");
        HttpURLConnection http = (HttpURLConnection) url.openConnection();
        http.setInstanceFollowRedirects(false);
        http.setConnectTimeout(15000);
        http.setReadTimeout(20000);
        http.setRequestProperty("Accept", "application/json");
        // The session stays in this process; it never enters an Intent or the notification.
        http.setRequestProperty("Cookie", "wh_session=" + cookie);
        try {
            if (http.getResponseCode() != 200) return;
            JSONArray items = new JSONObject(read(http.getInputStream())).optJSONArray("items");
            if (items == null) return;
            SharedPreferences prefs = context.getSharedPreferences("callie-waiting", Context.MODE_PRIVATE);
            Set<String> seen = new HashSet<>(prefs.getStringSet("seen", Collections.emptySet()));
            Set<String> current = new HashSet<>();
            NotificationManager manager = context.getSystemService(NotificationManager.class);
            String server = new URL(url.getProtocol(), url.getHost(), url.getPort(), "").toString();
            long now = System.currentTimeMillis();
            for (int i = 0; i < items.length(); i++) {
                JSONObject item = items.getJSONObject(i);
                String id = item.optString("id");
                if (id.isEmpty()) continue;
                current.add(id);
                if (seen.contains(id) || manager == null) continue;
                long created = 0;
                try { created = Instant.parse(item.optString("created_at").replace("+00:00", "Z")).toEpochMilli(); }
                catch (Exception ignored) { /* undated items still notify once */ created = now; }
                if (now - created > FRESH_MS) continue;
                manager.notify(id, 0, notification(context, item, server));
            }
            // Items no longer waiting (answered here or on the web) leave the shade.
            if (manager != null) for (String id : seen) if (!current.contains(id)) manager.cancel(id, 0);
            prefs.edit().putStringSet("seen", current).apply();
        } finally {
            http.disconnect();
        }
    }

    private static Notification notification(Context context, JSONObject item, String server) {
        String kind = item.optString("kind");
        String title = "question".equals(kind) ? "Callie has a question"
            : "approval".equals(kind) ? "Callie needs your approval" : "A decision is waiting on you";
        String text = item.optString("title");
        String thread = item.optString("session_title");
        if (!thread.isEmpty()) text = text.isEmpty() ? thread : text + " · " + thread;
        Intent open = new Intent(Intent.ACTION_VIEW, Uri.parse("calliope://open?server=" + Uri.encode(server)
            + "&session=" + Uri.encode(item.optString("session_id"))))
            .setPackage(context.getPackageName());
        PendingIntent tap = PendingIntent.getActivity(context, item.optString("id").hashCode(), open,
            PendingIntent.FLAG_IMMUTABLE | PendingIntent.FLAG_UPDATE_CURRENT);
        return new Notification.Builder(context, CHANNEL)
            .setSmallIcon(R.drawable.ic_notify)
            .setContentTitle(title)
            .setContentText(text)
            .setStyle(new Notification.BigTextStyle().bigText(text))
            .setCategory(Notification.CATEGORY_REMINDER)
            .setAutoCancel(true)
            .setContentIntent(tap)
            .build();
    }

    private static String read(InputStream stream) throws Exception {
        ByteArrayOutputStream out = new ByteArrayOutputStream();
        byte[] buffer = new byte[8192];
        int n;
        while ((n = stream.read(buffer)) > 0) {
            out.write(buffer, 0, n);
            if (out.size() > 256 * 1024) throw new java.io.IOException("Response too large");
        }
        return out.toString(StandardCharsets.UTF_8.name());
    }
}
