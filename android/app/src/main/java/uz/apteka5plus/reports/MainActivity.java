package uz.apteka5plus.reports;

import android.Manifest;
import android.app.Activity;
import android.app.DownloadManager;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;
import android.graphics.Insets;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.Environment;
import android.os.Handler;
import android.os.Looper;
import android.os.SystemClock;
import android.view.KeyEvent;
import android.view.View;
import android.view.WindowInsets;
import android.view.inputmethod.EditorInfo;
import android.view.inputmethod.InputMethodManager;
import android.webkit.CookieManager;
import android.webkit.JavascriptInterface;
import android.webkit.URLUtil;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.Button;
import android.widget.EditText;
import android.widget.ProgressBar;
import android.widget.TextView;
import android.widget.Toast;

import java.net.HttpURLConnection;
import java.net.URL;
import java.net.URLDecoder;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * «5+ Отчёты»: сайт отчётов аптеки (web_reports.py на компьютере с программой учёта)
 * в полноэкранном WebView. Адрес сервера вводится один раз и хранится в настройках.
 */
public class MainActivity extends Activity {

    private static final String PREFS = "settings";
    private static final String KEY_SERVER = "server";
    private static final int DEFAULT_PORT = 8080;
    private static final int REQ_STORAGE = 1;

    private WebView web;
    private View setup, error;
    private ProgressBar progress;
    private EditText address;
    private TextView setupStatus, errorText;
    private Button connect;

    private final Handler ui = new Handler(Looper.getMainLooper());
    private long pausedAt = 0;
    private PendingDownload pending;   // ждёт разрешения на запись (Android 7-9)

    private static class PendingDownload {
        final String url, name;
        PendingDownload(String url, String name) { this.url = url; this.name = name; }
    }

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        setContentView(R.layout.activity_main);
        web = findViewById(R.id.web);
        setup = findViewById(R.id.setup);
        error = findViewById(R.id.error);
        progress = findViewById(R.id.progress);
        address = findViewById(R.id.address);
        setupStatus = findViewById(R.id.setupStatus);
        errorText = findViewById(R.id.errorText);
        connect = findViewById(R.id.connect);

        applySystemBarInsets(findViewById(R.id.root));
        setupWebView();

        connect.setOnClickListener(v -> tryConnect());
        address.setOnEditorActionListener((v, actionId, event) -> {
            if (actionId == EditorInfo.IME_ACTION_GO) { tryConnect(); return true; }
            return false;
        });
        findViewById(R.id.retry).setOnClickListener(v -> openSite());
        findViewById(R.id.change).setOnClickListener(v -> showSetup(null));

        if (savedInstanceState != null) {
            web.restoreState(savedInstanceState);
        }
        if (server() == null) {
            showSetup(null);
        } else if (savedInstanceState == null || web.getUrl() == null) {
            openSite();
        } else {
            show(web);
        }
    }

    // ---------- экраны ----------

    private void show(View v) {
        web.setVisibility(v == web ? View.VISIBLE : View.GONE);
        setup.setVisibility(v == setup ? View.VISIBLE : View.GONE);
        error.setVisibility(v == error ? View.VISIBLE : View.GONE);
        if (v != web) progress.setVisibility(View.GONE);
    }

    private void showSetup(String status) {
        String s = server();
        address.setText(s == null ? "" : s.replaceFirst("^https?://", ""));
        address.setSelection(address.getText().length());
        setupStatus.setText(status == null ? "" : status);
        setupStatus.setTextColor(getColor(R.color.ink2));
        connect.setEnabled(true);
        show(setup);
    }

    private void showError(String detail) {
        errorText.setText(getString(R.string.error_text)
                + (detail == null ? "" : "\n\nАдрес: " + server() + "\n" + detail));
        show(error);
    }

    // ---------- адрес сервера ----------

    private SharedPreferences prefs() {
        return getSharedPreferences(PREFS, MODE_PRIVATE);
    }

    private String server() {
        return prefs().getString(KEY_SERVER, null);
    }

    /** «192.168.1.15» -> «http://192.168.1.15:8080»; лишние пробелы и «/» в конце убираем. */
    static String normalize(String raw) {
        String s = raw.trim().replaceAll("\\s+", "").replaceAll("/+$", "");
        if (s.isEmpty()) return null;
        if (!s.matches("^https?://.*")) s = "http://" + s;
        Uri u = Uri.parse(s);
        if (u.getHost() == null || u.getHost().isEmpty()) return null;
        if (u.getPort() == -1) s = u.getScheme() + "://" + u.getHost() + ":" + DEFAULT_PORT;
        else s = u.getScheme() + "://" + u.getHost() + ":" + u.getPort();
        return s;
    }

    private void tryConnect() {
        String url = normalize(address.getText().toString());
        if (url == null) {
            setupStatus.setText("Введите адрес, например 192.168.1.15:8080");
            setupStatus.setTextColor(getColor(R.color.crit));
            return;
        }
        hideKeyboard();
        connect.setEnabled(false);
        setupStatus.setText(getString(R.string.checking));
        setupStatus.setTextColor(getColor(R.color.ink2));
        new Thread(() -> {
            String problem = checkHealth(url);
            ui.post(() -> {
                connect.setEnabled(true);
                if (problem == null) {
                    prefs().edit().putString(KEY_SERVER, url).apply();
                    web.clearHistory();
                    openSite();
                } else {
                    setupStatus.setText("Не удалось подключиться к " + url + "\n" + problem
                            + "\n\nТелефон должен быть в Wi-Fi аптеки, программа учёта — запущена.");
                    setupStatus.setTextColor(getColor(R.color.crit));
                }
            });
        }).start();
    }

    /** null - сервер отвечает; иначе текст проблемы. */
    private static String checkHealth(String base) {
        HttpURLConnection c = null;
        try {
            c = (HttpURLConnection) new URL(base + "/api/health").openConnection();
            c.setConnectTimeout(4000);
            c.setReadTimeout(4000);
            int code = c.getResponseCode();
            if (code == 200 || code == 401) return null;   // 401 - на сайте пароль, адрес верный
            return "Сервер ответил кодом " + code;
        } catch (java.net.SocketTimeoutException e) {
            return "Нет ответа (время ожидания истекло)";
        } catch (java.net.ConnectException e) {
            return "Соединение отклонено: программа учёта не запущена?";
        } catch (Exception e) {
            return e.getClass().getSimpleName() + ": " + e.getMessage();
        } finally {
            if (c != null) c.disconnect();
        }
    }

    private void openSite() {
        String s = server();
        if (s == null) { showSetup(null); return; }
        show(web);
        progress.setVisibility(View.VISIBLE);
        web.loadUrl(s + "/");
    }

    // ---------- WebView ----------

    private void setupWebView() {
        WebSettings ws = web.getSettings();
        ws.setJavaScriptEnabled(true);
        ws.setDomStorageEnabled(true);          // тема и выбранный период запоминаются
        ws.setCacheMode(WebSettings.LOAD_NO_CACHE);
        ws.setUserAgentString(ws.getUserAgentString() + " Apteka5plusApp/1.0");
        web.setBackgroundColor(getColor(R.color.page));
        CookieManager.getInstance().setAcceptCookie(true);

        web.addJavascriptInterface(new Bridge(), "Android5plus");
        web.setWebChromeClient(new WebChromeClient());   // нужен для alert() на сайте
        web.setWebViewClient(new WebViewClient() {
            @Override
            public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
                Uri u = request.getUrl();
                String s = server();
                if (s != null && u.toString().startsWith(s)) return false;   // свой сайт - внутри
                try {
                    startActivity(new Intent(Intent.ACTION_VIEW, u));
                } catch (Exception ignored) {
                }
                return true;
            }

            @Override
            public void onPageFinished(WebView view, String url) {
                progress.setVisibility(View.GONE);
            }

            @Override
            public void onReceivedError(WebView view, WebResourceRequest request, WebResourceError err) {
                if (request.isForMainFrame()) {
                    showError(String.valueOf(err.getDescription()));
                }
            }
        });

        // «Excel за период» - сайт отдаёт файл, сохраняем его в «Загрузки»
        web.setDownloadListener((url, userAgent, contentDisposition, mimeType, contentLength) -> {
            String name = fileName(url, contentDisposition, mimeType);
            if (Build.VERSION.SDK_INT < Build.VERSION_CODES.Q
                    && checkSelfPermission(Manifest.permission.WRITE_EXTERNAL_STORAGE)
                    != PackageManager.PERMISSION_GRANTED) {
                pending = new PendingDownload(url, name);
                requestPermissions(new String[]{Manifest.permission.WRITE_EXTERNAL_STORAGE}, REQ_STORAGE);
                return;
            }
            download(url, name);
        });
    }

    /** Имя файла из Content-Disposition; русское имя приходит в filename*=UTF-8''... */
    static String fileName(String url, String contentDisposition, String mimeType) {
        if (contentDisposition != null) {
            Matcher m = Pattern.compile("filename\\*=UTF-8''([^;]+)", Pattern.CASE_INSENSITIVE)
                    .matcher(contentDisposition);
            if (m.find()) {
                try {
                    return URLDecoder.decode(m.group(1).trim(), "UTF-8");
                } catch (Exception ignored) {
                }
            }
        }
        return URLUtil.guessFileName(url, contentDisposition, mimeType);
    }

    private void download(String url, String name) {
        try {
            DownloadManager.Request r = new DownloadManager.Request(Uri.parse(url));
            r.setTitle(name);
            r.setDescription("Отчёт аптеки 5+");
            r.setMimeType("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet");
            r.addRequestHeader("User-Agent", web.getSettings().getUserAgentString());
            String cookies = CookieManager.getInstance().getCookie(url);
            if (cookies != null) r.addRequestHeader("Cookie", cookies);
            r.setNotificationVisibility(DownloadManager.Request.VISIBILITY_VISIBLE_NOTIFY_COMPLETED);
            r.setDestinationInExternalPublicDir(Environment.DIRECTORY_DOWNLOADS, name);
            ((DownloadManager) getSystemService(Context.DOWNLOAD_SERVICE)).enqueue(r);
            Toast.makeText(this, "Скачивается: " + name + "\nПапка «Загрузки»", Toast.LENGTH_LONG).show();
        } catch (Exception e) {
            Toast.makeText(this, "Не удалось скачать: " + e.getMessage(), Toast.LENGTH_LONG).show();
        }
    }

    @Override
    public void onRequestPermissionsResult(int requestCode, String[] permissions, int[] results) {
        super.onRequestPermissionsResult(requestCode, permissions, results);
        if (requestCode == REQ_STORAGE && pending != null) {
            if (results.length > 0 && results[0] == PackageManager.PERMISSION_GRANTED) {
                download(pending.url, pending.name);
            } else {
                Toast.makeText(this, "Без разрешения файл не сохранить", Toast.LENGTH_LONG).show();
            }
            pending = null;
        }
    }

    /** Вызывается из сайта: кнопка-шестерёнка видна только внутри приложения. */
    private class Bridge {
        @JavascriptInterface
        public void openSettings() {
            ui.post(() -> showSetup(null));
        }
    }

    // ---------- системное ----------

    /** targetSdk 35+: окно под статус-баром и навигацией - отступаем сами. */
    private void applySystemBarInsets(View root) {
        root.setOnApplyWindowInsetsListener((v, insets) -> {
            int top, bottom, left, right;
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
                Insets i = insets.getInsets(WindowInsets.Type.systemBars() | WindowInsets.Type.ime());
                top = i.top; bottom = i.bottom; left = i.left; right = i.right;
            } else {
                top = insets.getSystemWindowInsetTop();
                bottom = insets.getSystemWindowInsetBottom();
                left = insets.getSystemWindowInsetLeft();
                right = insets.getSystemWindowInsetRight();
            }
            v.setPadding(left, top, right, bottom);
            return insets;
        });
    }

    private void hideKeyboard() {
        InputMethodManager imm = (InputMethodManager) getSystemService(INPUT_METHOD_SERVICE);
        if (imm != null) imm.hideSoftInputFromWindow(address.getWindowToken(), 0);
    }

    @Override
    public boolean onKeyDown(int keyCode, KeyEvent event) {
        if (keyCode == KeyEvent.KEYCODE_BACK) {
            if (setup.getVisibility() == View.VISIBLE && server() != null) {
                openSite();
                return true;
            }
            if (web.getVisibility() == View.VISIBLE && web.canGoBack()) {
                web.goBack();
                return true;
            }
        }
        return super.onKeyDown(keyCode, event);
    }

    @Override
    protected void onPause() {
        super.onPause();
        pausedAt = SystemClock.elapsedRealtime();
        web.onPause();
    }

    @Override
    protected void onResume() {
        super.onResume();
        web.onResume();
        // вернулись после долгого перерыва - сразу свежие цифры
        if (pausedAt > 0 && SystemClock.elapsedRealtime() - pausedAt > 60_000
                && web.getVisibility() == View.VISIBLE) {
            web.reload();
        }
    }

    @Override
    protected void onSaveInstanceState(Bundle outState) {
        super.onSaveInstanceState(outState);
        web.saveState(outState);
    }
}
