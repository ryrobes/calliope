package ai.rvbbit.calliope.mobile;

import android.app.Activity;
import android.content.Intent;
import android.database.Cursor;
import android.net.Uri;
import android.provider.OpenableColumns;
import androidx.core.content.FileProvider;
import org.json.JSONObject;
import java.io.*;
import java.util.UUID;

/** User-selected URIs only. No storage permission or persistent URI grant. */
final class Attachments {
    static final int PICK = 710, CAMERA = 711;
    static void capture(CalliopeActivity activity, String path) {
        activity.runOnUiThread(() -> {
            try {
                File file = new File(path);
                Uri uri = FileProvider.getUriForFile(activity, activity.getPackageName()+".files", file);
                // Persist across Activity/process recreation while the camera owns focus.
                if (!activity.getSharedPreferences("camera",0).edit().putString("path",path).commit()) throw new IOException("Could not save capture target");
                Intent intent = new Intent(android.provider.MediaStore.ACTION_IMAGE_CAPTURE)
                    .putExtra(android.provider.MediaStore.EXTRA_OUTPUT,uri)
                    .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION | Intent.FLAG_GRANT_WRITE_URI_PERMISSION);
                intent.setClipData(android.content.ClipData.newRawUri("Calliope photo",uri));
                activity.startActivityForResult(intent,CAMERA);
            } catch (Exception e) {
                new File(path).delete();
                activity.getSharedPreferences("camera",0).edit().remove("path").commit();
                activity.attachmentPicked("{\"error\":\"Could not open the camera\"}");
            }
        });
    }
    static void cameraResult(CalliopeActivity activity, int result) {
        String path = activity.getSharedPreferences("camera",0).getString("path",null);
        if (path == null) { activity.attachmentPicked("{}"); return; }
        new Thread(() -> {
            File file = new File(path);
            try {
                if (result != Activity.RESULT_OK) { file.delete(); activity.attachmentPicked("{}"); return; }
                // Bound upload/decode cost and avoid retaining camera location metadata.
                android.graphics.BitmapFactory.Options options = new android.graphics.BitmapFactory.Options();
                options.inJustDecodeBounds=true;
                android.graphics.BitmapFactory.decodeFile(path,options);
                if(options.outWidth<=0 || options.outHeight<=0) throw new IOException("Empty photo");
                options.inSampleSize=1;
                while(Math.max(options.outWidth,options.outHeight)/options.inSampleSize>2048) options.inSampleSize*=2;
                options.inJustDecodeBounds=false;
                android.graphics.Bitmap bitmap=android.graphics.BitmapFactory.decodeFile(path,options);
                if(bitmap==null)throw new IOException("Unreadable photo");
                int orientation=new android.media.ExifInterface(path).getAttributeInt(android.media.ExifInterface.TAG_ORIENTATION,1);
                android.graphics.Matrix matrix=new android.graphics.Matrix();
                switch(orientation) {
                    case 2: matrix.setScale(-1,1);break;
                    case 3: matrix.setRotate(180);break;
                    case 4: matrix.setScale(1,-1);break;
                    case 5: matrix.setRotate(90);matrix.postScale(-1,1);break;
                    case 6: matrix.setRotate(90);break;
                    case 7: matrix.setRotate(270);matrix.postScale(-1,1);break;
                    case 8: matrix.setRotate(270);break;
                }
                android.graphics.Bitmap upright=android.graphics.Bitmap.createBitmap(bitmap,0,0,bitmap.getWidth(),bitmap.getHeight(),matrix,true);
                File normalized=new File(path+".tmp");
                try(FileOutputStream out=new FileOutputStream(normalized)) {
                    if(!upright.compress(android.graphics.Bitmap.CompressFormat.JPEG,85,out))throw new IOException("Could not encode photo");
                    out.getFD().sync();
                } finally { if(upright!=bitmap)upright.recycle();bitmap.recycle(); }
                if(normalized.length()>5*1024*1024 || !normalized.renameTo(file))throw new IOException("Could not save photo");
                try(FileOutputStream marker=new FileOutputStream(path.substring(0,path.lastIndexOf('.'))+".ready")) { marker.write(1);marker.getFD().sync(); }
                activity.attachmentPicked(new JSONObject().put("path",path).put("name",file.getName()).put("mime","image/jpeg").toString());
            } catch(Exception e) {
                file.delete();new File(path+".tmp").delete();
                activity.attachmentPicked("{\"error\":\"Could not save this photo. Your other draft changes are kept.\"}");
            } finally {
                activity.getSharedPreferences("camera",0).edit().remove("path").commit();
                try { activity.revokeUriPermission(FileProvider.getUriForFile(activity,activity.getPackageName()+".files",file),Intent.FLAG_GRANT_READ_URI_PERMISSION|Intent.FLAG_GRANT_WRITE_URI_PERMISSION); } catch(Exception ignored) {}
            }
        },"calliope-camera").start();
    }
    static void choose(CalliopeActivity activity) {
        activity.runOnUiThread(() -> {
            try {
                Intent intent = new Intent(Intent.ACTION_OPEN_DOCUMENT).addCategory(Intent.CATEGORY_OPENABLE).setType("*/*");
                intent.putExtra(Intent.EXTRA_MIME_TYPES,new String[]{"image/png","image/jpeg","application/pdf","text/plain"});
                activity.startActivityForResult(intent,PICK);
            } catch (Exception e) { activity.attachmentPicked("{\"error\":\"No file picker is available\"}"); }
        });
    }
    static void result(CalliopeActivity activity,int result,Intent data) {
        if(result!=Activity.RESULT_OK || data==null || data.getData()==null) {activity.attachmentPicked("{}");return;}
        Uri uri=data.getData();
        new Thread(() -> {
            File file=null;
            try {
                String name="attachment",mime=activity.getContentResolver().getType(uri);
                try(Cursor cursor=activity.getContentResolver().query(uri,new String[]{OpenableColumns.DISPLAY_NAME},null,null,null)) {
                    if(cursor!=null && cursor.moveToFirst())name=cursor.getString(0);
                }
                File folder=new File(activity.getCacheDir(),"attachments");folder.mkdirs();
                file=new File(folder,UUID.randomUUID().toString());
                try(InputStream in=activity.getContentResolver().openInputStream(uri);OutputStream out=new FileOutputStream(file)) {
                    byte[] buffer=new byte[8192];int count,total=0;
                    while((count=in.read(buffer))!=-1) {total+=count;if(total>5*1024*1024)throw new IOException("Choose a file up to 5 MB");out.write(buffer,0,count);}
                }
                activity.attachmentPicked(new JSONObject().put("path",file.getAbsolutePath()).put("name",name).put("mime",mime==null?"application/octet-stream":mime).toString());
            } catch(Exception e) {
                if(file!=null)file.delete();
                activity.attachmentPicked("{\"error\":\"Could not read this file. Choose a PNG, JPEG, PDF or text file up to 5 MB.\"}");
            }
        },"calliope-file-picker").start();
    }
    static void open(CalliopeActivity activity,String path,String mime) {
        activity.runOnUiThread(() -> {
            try {
                Uri uri=FileProvider.getUriForFile(activity,activity.getPackageName()+".files",new File(path));
                activity.startActivity(new Intent(Intent.ACTION_VIEW).setDataAndType(uri,mime).addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION));
            } catch(Exception e) {android.widget.Toast.makeText(activity,"No installed app can open this file",android.widget.Toast.LENGTH_LONG).show();}
        });
    }
}
