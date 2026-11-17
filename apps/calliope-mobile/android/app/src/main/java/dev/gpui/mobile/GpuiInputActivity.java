// Adapted from gpui-mobile 431d8cd; Apache-2.0. See THIRD_PARTY.md.
package dev.gpui.mobile;

import android.app.Activity;
import android.content.pm.PackageManager;
import android.os.Bundle;
import android.text.Editable;
import android.text.InputType;
import android.text.Selection;
import android.text.TextWatcher;
import android.view.KeyEvent;
import android.view.ViewGroup;
import android.view.inputmethod.BaseInputConnection;
import android.view.inputmethod.EditorInfo;
import android.view.inputmethod.InputConnection;
import android.view.inputmethod.InputConnectionWrapper;
import android.view.inputmethod.InputMethodManager;
import android.widget.EditText;

/** NativeActivity with a UI-thread InputConnection for multistage IMEs. */
public class GpuiInputActivity extends Activity {
    private InputProxy input;
    private String fieldKind = "text";

    private int fieldInputType() {
        switch (fieldKind) {
            case "integer": return InputType.TYPE_CLASS_NUMBER | InputType.TYPE_NUMBER_FLAG_SIGNED;
            case "quantity": case "money": return InputType.TYPE_CLASS_NUMBER | InputType.TYPE_NUMBER_FLAG_SIGNED | InputType.TYPE_NUMBER_FLAG_DECIMAL;
            case "email": return InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_EMAIL_ADDRESS;
            case "url": return InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_URI;
            case "password": return InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_PASSWORD;
            case "date": return InputType.TYPE_CLASS_DATETIME | InputType.TYPE_DATETIME_VARIATION_DATE;
            case "time": return InputType.TYPE_CLASS_DATETIME | InputType.TYPE_DATETIME_VARIATION_TIME;
            case "longText": return InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_FLAG_MULTI_LINE;
            default: return InputType.TYPE_CLASS_TEXT;
        }
    }

    public void gpuiSetInputKind(String kind) {
        runOnUiThread(() -> {
            if (fieldKind.equals(kind)) return;
            fieldKind = kind;
            if (input == null) return;
            // The GPUI editing buffer owns selection/composition; do not reset it
            // just to switch the layout of the software keyboard.
            input.setRawInputType(fieldInputType());
            input.setImeOptions(EditorInfo.IME_FLAG_NO_EXTRACT_UI | ("longText".equals(fieldKind) ? EditorInfo.IME_ACTION_NONE : EditorInfo.IME_ACTION_DONE));
            ((InputMethodManager)getSystemService(INPUT_METHOD_SERVICE)).restartInput(input);
        });
    }

    @Override protected void onCreate(Bundle state) {
        // NativeActivity's dlopen alone does not register JNI native methods.
        try {
            String library = getPackageManager().getActivityInfo(getComponentName(),
                    PackageManager.GET_META_DATA).metaData.getString("android.app.lib_name");
            if (library != null) System.loadLibrary(library);
        } catch (PackageManager.NameNotFoundException error) {
            throw new IllegalStateException(error);
        }
        super.onCreate(state);
    }

    public void gpuiShowKeyboard(int keyboardType, long session) {
        runOnUiThread(() -> {
            if (input == null) {
                input = new InputProxy();
                input.setAlpha(0f);
                input.setPadding(0, 0, 0, 0);
                addContentView(input, new ViewGroup.LayoutParams(1, 1));
            }
            input.reset(session);
            int type = InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_FLAG_MULTI_LINE;
            switch (keyboardType) {
                case 1: type = InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_EMAIL_ADDRESS; break;
                case 2: type = InputType.TYPE_CLASS_PHONE; break;
                case 3: type = InputType.TYPE_CLASS_NUMBER; break;
                case 4: type = InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_URI; break;
                case 5: type = InputType.TYPE_CLASS_NUMBER | InputType.TYPE_NUMBER_FLAG_DECIMAL; break;
            }
            input.setInputType(keyboardType == 0 ? fieldInputType() : type);
            input.setImeOptions(EditorInfo.IME_FLAG_NO_EXTRACT_UI | ("longText".equals(fieldKind) ? EditorInfo.IME_ACTION_NONE : EditorInfo.IME_ACTION_DONE));
            input.requestFocus();
            InputMethodManager imm = (InputMethodManager) getSystemService(INPUT_METHOD_SERVICE);
            imm.restartInput(input);
            imm.showSoftInput(input, InputMethodManager.SHOW_IMPLICIT);
        });
    }

    public void gpuiHideKeyboard(long session) {
        runOnUiThread(() -> {
            if (input == null) return;
            input.reset(session);
            InputMethodManager imm = (InputMethodManager) getSystemService(INPUT_METHOD_SERVICE);
            imm.hideSoftInputFromWindow(input.getWindowToken(), 0);
            input.clearFocus();
        });
    }

    public void gpuiResetComposition(long session) {
        runOnUiThread(() -> {
            if (input == null) return;
            input.reset(session);
            ((InputMethodManager) getSystemService(INPUT_METHOD_SERVICE)).restartInput(input);
        });
    }

    private final class InputProxy extends EditText {
        private long session;
        private int depth;
        private boolean marked;

        InputProxy() {
            super(GpuiInputActivity.this);
            addTextChangedListener(new TextWatcher() {
                public void beforeTextChanged(CharSequence s, int start, int count, int after) {}
                public void onTextChanged(CharSequence s, int start, int before, int count) {}
                public void afterTextChanged(Editable text) {
                    // Hardware keyboards edit the widget directly, outside its
                    // InputConnection. IME mutations are batched by depth below.
                    if (depth == 0) { depth++; endEdit(); }
                }
            });
        }

        @Override public boolean onKeyDown(int code, KeyEvent event) {
            if (code == KeyEvent.KEYCODE_DEL && getText().length() == 0 && !marked) {
                mobileIme(session, 3, "", 1, 0);
                return true;
            }
            // Physical keys are already committed characters. Passing them
            // through the shadow EditText applies capitalization and selection
            // against a buffer cleared after each commit, corrupting fast typing.
            // Keep software IME composition on the InputConnection path below.
            int character = event.getUnicodeChar();
            if (!event.isCtrlPressed() && !event.isAltPressed()
                    && (code == KeyEvent.KEYCODE_ENTER || Character.isValidCodePoint(character) && !Character.isISOControl(character))) {
                if (marked) {
                    depth++;
                    BaseInputConnection.removeComposingSpans(getText());
                    endEdit();
                }
                if (code == KeyEvent.KEYCODE_ENTER && !"longText".equals(fieldKind)) {
                    mobileIme(session, 4, "", 0, 0);
                } else {
                    String value = code == KeyEvent.KEYCODE_ENTER ? "\n" : new String(Character.toChars(character));
                    mobileIme(session, 1, value, value.length(), value.length());
                }
                return true;
            }
            return super.onKeyDown(code, event);
        }

        void reset(long nextSession) {
            depth++;
            getText().clear();
            marked = false;
            session = nextSession;
            depth = 0;
        }

        private void endEdit() {
            if (--depth != 0) return;
            Editable text = getText();
            boolean composing = BaseInputConnection.getComposingSpanStart(text) >= 0;
            if (composing || marked || text.length() > 0) {
                mobileIme(session, composing ? 0 : 1, text.toString(),
                        Math.max(0, Selection.getSelectionStart(text)),
                        Math.max(0, Selection.getSelectionEnd(text)));
                marked = composing;
                if (!composing) {
                    depth++;
                    text.clear();
                    depth--;
                }
            }
        }

        @Override public boolean onKeyPreIme(int code, KeyEvent event) {
            if (code == KeyEvent.KEYCODE_BACK && event.getAction() == KeyEvent.ACTION_UP) {
                mobileIme(session, 4, "", 0, 0);
            }
            // Hardware text must bypass predictive IMEs: they see only this
            // transient composition buffer, not GPUI's full document/cursor.
            int character = event.getUnicodeChar();
            if (!event.isCtrlPressed() && !event.isAltPressed()
                    && (code == KeyEvent.KEYCODE_ENTER || code == KeyEvent.KEYCODE_DEL
                        || Character.isValidCodePoint(character) && !Character.isISOControl(character))) {
                if (event.getAction() == KeyEvent.ACTION_DOWN) return onKeyDown(code, event);
                return true;
            }
            return super.onKeyPreIme(code, event);
        }

        @Override public InputConnection onCreateInputConnection(EditorInfo info) {
            InputConnection connection = super.onCreateInputConnection(info);
            if (connection == null) return null;
            final long connectionSession = session;
            return new InputConnectionWrapper(connection, false) {
                @Override public boolean beginBatchEdit() {
                    if (connectionSession != session) return false;
                    depth++;
                    return super.beginBatchEdit();
                }
                @Override public boolean endBatchEdit() {
                    if (connectionSession != session) return false;
                    boolean result = super.endBatchEdit();
                    if (depth > 0) endEdit();
                    return result;
                }
                @Override public boolean setComposingText(CharSequence text, int cursor) {
                    if (connectionSession != session) return false;
                    depth++;
                    try { return super.setComposingText(text, cursor); }
                    finally { endEdit(); }
                }
                @Override public boolean setComposingRegion(int start, int end) {
                    if (connectionSession != session) return false;
                    depth++;
                    try { return super.setComposingRegion(start, end); }
                    finally { endEdit(); }
                }
                @Override public boolean finishComposingText() {
                    if (connectionSession != session) return false;
                    depth++;
                    try { return super.finishComposingText(); }
                    finally { endEdit(); }
                }
                @Override public boolean commitText(CharSequence text, int cursor) {
                    if (connectionSession != session) return false;
                    depth++;
                    try { return super.commitText(text, cursor); }
                    finally { endEdit(); }
                }
                @Override public boolean setSelection(int start, int end) {
                    if (connectionSession != session) return false;
                    depth++;
                    try { return super.setSelection(start, end); }
                    finally { endEdit(); }
                }
                @Override public boolean deleteSurroundingText(int before, int after) {
                    if (connectionSession != session) return false;
                    if (getText().length() == 0 && !marked) {
                        mobileIme(session, 2, "", before, after);
                        return true;
                    }
                    depth++;
                    try { return super.deleteSurroundingText(before, after); }
                    finally { endEdit(); }
                }
                @Override public boolean deleteSurroundingTextInCodePoints(int before, int after) {
                    if (connectionSession != session) return false;
                    if (getText().length() == 0 && !marked) {
                        mobileIme(session, 3, "", before, after);
                        return true;
                    }
                    depth++;
                    try { return super.deleteSurroundingTextInCodePoints(before, after); }
                    finally { endEdit(); }
                }
                @Override public boolean sendKeyEvent(KeyEvent event) {
                    if (connectionSession != session) return false;
                    if (event.getKeyCode() == KeyEvent.KEYCODE_DEL) {
                        if (event.getAction() == KeyEvent.ACTION_DOWN) deleteSurroundingText(1, 0);
                        return true;
                    }
                    if (event.getKeyCode() == KeyEvent.KEYCODE_ENTER) {
                        if (event.getAction() == KeyEvent.ACTION_DOWN) commitText("\n", 1);
                        return true;
                    }
                    return super.sendKeyEvent(event);
                }
                @Override public boolean performEditorAction(int action) {
                    if (connectionSession != session) return false;
                    if (action == EditorInfo.IME_ACTION_DONE) {
                        finishComposingText();
                        mobileIme(session, 4, "", 0, 0);
                        return true;
                    }
                    return commitText("\n", 1);
                }
            };
        }
    }

    private static native void mobileIme(long session, int kind, String text, int start, int end);
}
