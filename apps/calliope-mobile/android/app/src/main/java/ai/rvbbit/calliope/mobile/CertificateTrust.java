package ai.rvbbit.calliope.mobile;

import android.util.Base64;
import java.security.KeyStore;
import java.security.cert.X509Certificate;
import javax.net.ssl.TrustManager;
import javax.net.ssl.TrustManagerFactory;
import javax.net.ssl.X509TrustManager;

/** Share Android's configured trust anchors with the Rust HTTP client. */
final class CertificateTrust {
    static String certificates() throws Exception {
        TrustManagerFactory factory = TrustManagerFactory.getInstance(TrustManagerFactory.getDefaultAlgorithm());
        // null selects Android's default trust manager and network security config.
        factory.init((KeyStore) null);
        StringBuilder pem = new StringBuilder();
        for (TrustManager manager : factory.getTrustManagers()) {
            if (!(manager instanceof X509TrustManager)) continue;
            for (X509Certificate certificate : ((X509TrustManager) manager).getAcceptedIssuers()) {
                pem.append("-----BEGIN CERTIFICATE-----\n")
                    .append(Base64.encodeToString(certificate.getEncoded(), Base64.NO_WRAP))
                    .append("\n-----END CERTIFICATE-----\n");
            }
        }
        if (pem.length() == 0) throw new java.security.KeyStoreException("Android has no trusted certificates");
        return pem.toString();
    }
}
