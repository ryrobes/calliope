package ai.rvbbit.calliope.mobile;

import android.content.Context;
import android.security.keystore.KeyGenParameterSpec;
import android.security.keystore.KeyProperties;
import android.util.AtomicFile;
import android.util.Base64;
import java.io.*;
import java.nio.charset.StandardCharsets;
import java.security.KeyStore;
import javax.crypto.*;
import javax.crypto.spec.GCMParameterSpec;
import org.json.JSONObject;

/** Session encryption keys remain in Android Keystore, outside app files. */
final class ConnectionStore {
    private static final String ALIAS = "calliope.connection.v1";
    private static SecretKey key() throws Exception {
        KeyStore store = KeyStore.getInstance("AndroidKeyStore");
        store.load(null);
        if (store.containsAlias(ALIAS)) return (SecretKey) store.getKey(ALIAS, null);
        KeyGenerator generator = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore");
        generator.init(new KeyGenParameterSpec.Builder(ALIAS, KeyProperties.PURPOSE_ENCRYPT | KeyProperties.PURPOSE_DECRYPT)
            .setBlockModes(KeyProperties.BLOCK_MODE_GCM).setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE).build());
        return generator.generateKey();
    }
    static synchronized void save(Context context, String value) throws Exception {
        Cipher cipher = Cipher.getInstance("AES/GCM/NoPadding");
        cipher.init(Cipher.ENCRYPT_MODE, key());
        byte[] encrypted = cipher.doFinal(value.getBytes(StandardCharsets.UTF_8));
        JSONObject envelope = new JSONObject().put("v", 1)
            .put("iv", Base64.encodeToString(cipher.getIV(), Base64.NO_WRAP))
            .put("data", Base64.encodeToString(encrypted, Base64.NO_WRAP));
        AtomicFile file = new AtomicFile(new File(context.getFilesDir(), "connection.enc"));
        FileOutputStream stream = file.startWrite();
        try { stream.write(envelope.toString().getBytes(StandardCharsets.UTF_8)); file.finishWrite(stream); }
        catch (Exception error) { file.failWrite(stream); throw error; }
    }
    static synchronized String load(Context context) throws Exception {
        File legacy = new File(context.getFilesDir(), "connection.json");
        if (legacy.isFile()) {
            String value = new String(java.nio.file.Files.readAllBytes(legacy.toPath()), StandardCharsets.UTF_8);
            save(context, value);
            if (!legacy.delete()) throw new IOException("Cannot remove migrated connection");
            return value;
        }
        AtomicFile file = new AtomicFile(new File(context.getFilesDir(), "connection.enc"));
        if (!file.getBaseFile().exists()) return "{}";
        JSONObject envelope = new JSONObject(new String(file.readFully(), StandardCharsets.UTF_8));
        Cipher cipher = Cipher.getInstance("AES/GCM/NoPadding");
        cipher.init(Cipher.DECRYPT_MODE, key(), new GCMParameterSpec(128, Base64.decode(envelope.getString("iv"), Base64.NO_WRAP)));
        return new String(cipher.doFinal(Base64.decode(envelope.getString("data"), Base64.NO_WRAP)), StandardCharsets.UTF_8);
    }
}
