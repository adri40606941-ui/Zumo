package com.zumo.tunnel;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.content.IntentFilter;
import android.content.SharedPreferences;
import android.net.VpnService;
import android.os.Build;
import android.os.Bundle;
import android.widget.Button;
import android.widget.EditText;
import android.widget.TextView;
import android.widget.Toast;

import androidx.appcompat.app.AppCompatActivity;

public class MainActivity extends AppCompatActivity {

    private static final int VPN_REQUEST = 1001;
    private static final String PREFS = "zumo_prefs";

    private EditText etPayload, etHost, etPort, etUser, etPass;
    private Button btnConnect;
    private TextView tvLog, tvState;

    private boolean connected = false;
    private String pendingEngine = "hev"; // "hev" | "badvpn"

    private final BroadcastReceiver receiver = new BroadcastReceiver() {
        @Override public void onReceive(Context ctx, Intent intent) {
            String action = intent.getAction();
            if ("com.zumo.tunnel.LOG".equals(action)) {
                appendLog(intent.getStringExtra("line"));
            } else if ("com.zumo.tunnel.STATE".equals(action)) {
                connected = intent.getBooleanExtra("connected", false);
                refreshState();
            }
        }
    };

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        setContentView(R.layout.activity_main);

        etPayload = findViewById(R.id.etPayload);
        etHost    = findViewById(R.id.etHost);
        etPort    = findViewById(R.id.etPort);
        etUser    = findViewById(R.id.etUser);
        etPass    = findViewById(R.id.etPass);
        btnConnect = findViewById(R.id.btnConnect);
        tvLog     = findViewById(R.id.tvLog);
        tvState   = findViewById(R.id.tvState);

        loadPrefs();
        refreshState();

        btnConnect.setOnClickListener(v -> {
            if (connected) {
                stopTunnel();
            } else {
                savePrefs();
                askEngineThenConnect();
            }
        });
    }

    /** Pregunta qué motor usar y después sigue con el flujo de conexión. */
    private void askEngineThenConnect() {
        if (etHost.getText().toString().trim().isEmpty()
                || etUser.getText().toString().trim().isEmpty()) {
            Toast.makeText(this, "Completá host y usuario SSH", Toast.LENGTH_SHORT).show();
            return;
        }
        final String[] labels = { "hev-socks5-tunnel (recomendado)", "badvpn-tun2socks" };
        final String[] values = { "hev", "badvpn" };
        new android.app.AlertDialog.Builder(this)
                .setTitle("Elegí el motor VPN")
                .setItems(labels, (dialog, which) -> {
                    pendingEngine = values[which];
                    startTunnelFlow();
                })
                .show();
    }

    private void startTunnelFlow() {
        // Ask the user to authorise the VPN (system dialog).
        Intent prepare = VpnService.prepare(this);
        if (prepare != null) {
            startActivityForResult(prepare, VPN_REQUEST);
        } else {
            onActivityResult(VPN_REQUEST, RESULT_OK, null);
        }
    }

    @Override
    protected void onActivityResult(int requestCode, int resultCode, Intent data) {
        super.onActivityResult(requestCode, resultCode, data);
        if (requestCode == VPN_REQUEST && resultCode == RESULT_OK) {
            appendLog("Iniciando túnel...");
            Intent i = new Intent(this, TunnelVpnService.class)
                    .setAction(TunnelVpnService.ACTION_START)
                    .putExtra(TunnelVpnService.EXTRA_HOST, etHost.getText().toString().trim())
                    .putExtra(TunnelVpnService.EXTRA_PORT, parsePort())
                    .putExtra(TunnelVpnService.EXTRA_USER, etUser.getText().toString().trim())
                    .putExtra(TunnelVpnService.EXTRA_PASS, etPass.getText().toString())
                    .putExtra(TunnelVpnService.EXTRA_PAYLOAD, etPayload.getText().toString())
                    .putExtra(TunnelVpnService.EXTRA_ENGINE, pendingEngine);
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                startForegroundService(i);
            } else {
                startService(i);
            }
        } else if (requestCode == VPN_REQUEST) {
            Toast.makeText(this, "Permiso de VPN denegado", Toast.LENGTH_SHORT).show();
        }
    }

    private void stopTunnel() {
        Intent i = new Intent(this, TunnelVpnService.class)
                .setAction(TunnelVpnService.ACTION_STOP);
        startService(i);
    }

    private int parsePort() {
        try { return Integer.parseInt(etPort.getText().toString().trim()); }
        catch (Exception e) { return 22; }
    }

    private void refreshState() {
        tvState.setText(connected ? "● Conectado" : "○ Desconectado");
        btnConnect.setText(connected ? "Desconectar" : "Conectar");
    }

    private void appendLog(String line) {
        if (line == null) return;
        runOnUiThread(() -> tvLog.append(line + "\n"));
    }

    private void loadPrefs() {
        SharedPreferences p = getSharedPreferences(PREFS, MODE_PRIVATE);
        etPayload.setText(p.getString("payload", ""));
        etHost.setText(p.getString("host", ""));
        etPort.setText(p.getString("port", "22"));
        etUser.setText(p.getString("user", ""));
        etPass.setText(p.getString("pass", ""));
    }

    private void savePrefs() {
        getSharedPreferences(PREFS, MODE_PRIVATE).edit()
                .putString("payload", etPayload.getText().toString())
                .putString("host", etHost.getText().toString())
                .putString("port", etPort.getText().toString())
                .putString("user", etUser.getText().toString())
                .putString("pass", etPass.getText().toString())
                .apply();
    }

    @Override protected void onResume() {
        super.onResume();
        IntentFilter f = new IntentFilter();
        f.addAction("com.zumo.tunnel.LOG");
        f.addAction("com.zumo.tunnel.STATE");
        if (Build.VERSION.SDK_INT >= 33) {
            registerReceiver(receiver, f, Context.RECEIVER_NOT_EXPORTED);
        } else {
            registerReceiver(receiver, f);
        }
    }

    @Override protected void onPause() {
        super.onPause();
        try { unregisterReceiver(receiver); } catch (Exception ignored) { }
    }
}
