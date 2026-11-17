import AVFoundation
import UIKit
import PhotosUI
import QuickLook
import UniformTypeIdentifiers

/// System pickers copy into private storage; the Rust draft journal owns uploads.
final class NativeMedia: NSObject, UIImagePickerControllerDelegate, UINavigationControllerDelegate,
                         UIDocumentPickerDelegate, PHPickerViewControllerDelegate,
                         QLPreviewControllerDataSource, UIAdaptivePresentationControllerDelegate {
    private static let shared = NativeMedia()
    private var completion: (([String: Any]) -> Void)?
    private var camera: URL?
    private weak var activePicker: UIViewController?
    private var processingSelection = false
    private var previewURL: URL?
    private weak var previewController: UIViewController?
    private static let limit = 5 * 1024 * 1024
    private static func error(_ message: String) -> NSError {
        NSError(domain: "CalliopeMedia", code: 1, userInfo: [NSLocalizedDescriptionKey: message])
    }
    private static func presenter() throws -> UIViewController {
        guard var controller = (UIApplication.shared.delegate as? AppDelegate)?.window?.rootViewController else { throw error("Calliope is not visible") }
        while let next = controller.presentedViewController { controller = next }
        return controller
    }
    private static func privateURL(_ path: String) throws -> URL {
        let url = URL(fileURLWithPath: path).resolvingSymlinksInPath()
        let home = URL(fileURLWithPath: NSHomeDirectory()).resolvingSymlinksInPath().path + "/"
        guard url.path.hasPrefix(home), !path.isEmpty else { throw error("Attachment is outside private app storage") }
        return url
    }
    static func pick(_ request: [String: Any]) throws -> [String: Any] {
        guard !Thread.isMainThread else { throw error("The file picker must run on an attachment worker") }
        let signal = DispatchSemaphore(value: 0)
        var reply: [String: Any] = [:]
        DispatchQueue.main.async {
            guard shared.completion == nil else { reply = ["error": "An attachment picker is already open"]; signal.signal(); return }
            shared.completion = { reply = $0; signal.signal() }; shared.processingSelection = false
            do {
                if let path = request["camera"] as? String {
                    shared.camera = try privateURL(path)
                    guard UIImagePickerController.isSourceTypeAvailable(.camera) else {
                        throw error("This device has no camera. Use Attach → Photo library or Files instead.")
                    }
                    switch AVCaptureDevice.authorizationStatus(for: .video) {
                    case .authorized: try shared.showCamera()
                    case .notDetermined:
                        AVCaptureDevice.requestAccess(for: .video) { allowed in
                            DispatchQueue.main.async {
                                do { guard allowed else { throw error("Camera access was denied. Enable Camera for Calliope in iOS Settings.") }; try shared.showCamera() }
                                catch { shared.finish(["error": error.localizedDescription]) }
                            }
                        }
                    default: throw error("Enable Camera for Calliope in iOS Settings.")
                    }
                } else {
                    shared.camera = nil
                    let sheet = UIAlertController(title: "Attach to your draft", message: "Photos, PDF or text · up to 5 MB", preferredStyle: .actionSheet)
                    sheet.addAction(UIAlertAction(title: "Photo library", style: .default) { _ in shared.photos() })
                    sheet.addAction(UIAlertAction(title: "Files", style: .default) { _ in shared.documents() })
                    sheet.addAction(UIAlertAction(title: "Cancel", style: .cancel) { _ in shared.finish([:]) })
                    let parent = try presenter()
                    sheet.popoverPresentationController?.sourceView = parent.view
                    sheet.popoverPresentationController?.sourceRect = CGRect(x: parent.view.bounds.midX, y: parent.view.bounds.maxY - 30, width: 1, height: 1)
                    shared.activePicker = sheet
                    parent.present(sheet, animated: true)
                    sheet.presentationController?.delegate = shared
                }
            } catch { shared.finish(["error": error.localizedDescription]) }
        }
        signal.wait()
        return reply
    }
    private func finish(_ value: [String: Any]) {
        let callback = completion; completion = nil
        if value["path"] == nil, let camera { try? FileManager.default.removeItem(at: camera) }
        camera = nil; activePicker = nil; processingSelection = false; callback?(value)
    }
    private func showCamera() throws {
        let picker = UIImagePickerController(); picker.sourceType = .camera
        picker.cameraCaptureMode = .photo; picker.delegate = self
        activePicker = picker
        try Self.presenter().present(picker, animated: true)
    }
    private func photos() {
        do {
            var config = PHPickerConfiguration(); config.selectionLimit = 1; config.filter = .images
            let picker = PHPickerViewController(configuration: config); picker.delegate = self
            activePicker = picker
            try Self.presenter().present(picker, animated: true)
            picker.presentationController?.delegate = self
        } catch { finish(["error": error.localizedDescription]) }
    }
    private func documents() {
        do {
            let picker = UIDocumentPickerViewController(forOpeningContentTypes: [.png, .jpeg, .pdf, .plainText], asCopy: true)
            picker.allowsMultipleSelection = false; picker.delegate = self
            activePicker = picker
            try Self.presenter().present(picker, animated: true)
            picker.presentationController?.delegate = self
        } catch { finish(["error": error.localizedDescription]) }
    }
    private func savePhoto(_ image: UIImage) throws -> [String: Any] {
        let scale = min(1, 2048 / max(image.size.width, image.size.height))
        let size = CGSize(width: image.size.width * scale, height: image.size.height * scale)
        let format = UIGraphicsImageRendererFormat(); format.scale = 1; format.opaque = true
        let normalized = UIGraphicsImageRenderer(size: size, format: format).image { _ in image.draw(in: CGRect(origin: .zero, size: size)) }
        guard let bytes = normalized.jpegData(compressionQuality: 0.85), !bytes.isEmpty, bytes.count <= Self.limit else { throw Self.error("Choose a photo up to 5 MB") }
        let target = camera ?? FileManager.default.temporaryDirectory.appendingPathComponent("Photo-\(UUID().uuidString).jpg")
        try bytes.write(to: target, options: [.atomic, .completeFileProtectionUntilFirstUserAuthentication])
        if camera != nil {
            // The shared journal recovers only explicitly confirmed, fully written captures.
            try Data("ready".utf8).write(to: target.deletingPathExtension().appendingPathExtension("ready"), options: .atomic)
        }
        return ["path": target.path, "name": target.lastPathComponent, "mime": "image/jpeg"]
    }
    func imagePickerControllerDidCancel(_ picker: UIImagePickerController) { picker.dismiss(animated: true); finish([:]) }
    func imagePickerController(_ picker: UIImagePickerController, didFinishPickingMediaWithInfo info: [UIImagePickerController.InfoKey: Any]) {
        picker.dismiss(animated: true)
        do {
            guard let image = info[.originalImage] as? UIImage else { throw Self.error("Camera returned no photo") }
            finish(try savePhoto(image))
        } catch { finish(["error": error.localizedDescription]) }
    }
    func picker(_ picker: PHPickerViewController, didFinishPicking results: [PHPickerResult]) {
        processingSelection = true
        picker.presentationController?.delegate = nil
        picker.dismiss(animated: true)
        guard let result = results.first else { finish([:]); return }
        result.itemProvider.loadObject(ofClass: UIImage.self) { image, error in
            DispatchQueue.main.async {
                do {
                    if let error { throw error }
                    guard let image = image as? UIImage else { throw Self.error("Photo could not be opened") }
                    self.finish(try self.savePhoto(image))
                } catch { self.finish(["error": error.localizedDescription]) }
            }
        }
    }
    func documentPickerWasCancelled(_ controller: UIDocumentPickerViewController) { finish([:]) }
    func presentationControllerDidDismiss(_ presentationController: UIPresentationController) {
        if !processingSelection && presentationController.presentedViewController === activePicker { finish([:]) }
    }
    func documentPicker(_ controller: UIDocumentPickerViewController, didPickDocumentsAt urls: [URL]) {
        do {
            guard let source = urls.first else { finish([:]); return }
            let access = source.startAccessingSecurityScopedResource()
            defer { if access { source.stopAccessingSecurityScopedResource() } }
            let size = try source.resourceValues(forKeys: [.fileSizeKey]).fileSize ?? 0
            guard size > 0, size <= Self.limit else { throw Self.error("Choose a file up to 5 MB") }
            let suffix = source.pathExtension.lowercased()
            guard let mime = ["jpg":"image/jpeg", "jpeg":"image/jpeg", "png":"image/png", "pdf":"application/pdf", "txt":"text/plain"][suffix] else { throw Self.error("Choose PNG, JPEG, PDF or UTF-8 text") }
            let folder = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString, isDirectory: true)
            try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
            let target = folder.appendingPathComponent(source.lastPathComponent)
            try FileManager.default.copyItem(at: source, to: target)
            finish(["path": target.path, "name": target.lastPathComponent, "mime": mime])
        } catch { finish(["error": error.localizedDescription]) }
    }
    static func preview(_ request: [String: Any]) throws -> [String: Any] {
        let source = try privateURL(request["path"] as? String ?? "")
        let suffix = UTType(mimeType: request["mime"] as? String ?? "")?.preferredFilenameExtension ?? source.pathExtension
        let folder = FileManager.default.temporaryDirectory.appendingPathComponent("previews", isDirectory: true)
        try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        let target = folder.appendingPathComponent("Attachment-\(UUID().uuidString)").appendingPathExtension(suffix)
        try FileManager.default.copyItem(at: source, to: target)
        DispatchQueue.main.async {
            if let old = shared.previewURL { try? FileManager.default.removeItem(at: old) }
            shared.previewURL = target
            let preview = QLPreviewController(); preview.dataSource = shared
            preview.navigationItem.rightBarButtonItem = UIBarButtonItem(barButtonSystemItem: .done, target: shared, action: #selector(closePreview))
            let navigation = UINavigationController(rootViewController: preview)
            navigation.modalPresentationStyle = .fullScreen
            shared.previewController = navigation
            if let parent = try? presenter() { parent.present(navigation, animated: true) }
        }
        return ["ok": true]
    }
    @objc private func closePreview() {
        previewController?.dismiss(animated: true) {
            if let url = self.previewURL { try? FileManager.default.removeItem(at: url) }
            self.previewURL = nil
        }
    }
    func numberOfPreviewItems(in controller: QLPreviewController) -> Int { previewURL == nil ? 0 : 1 }
    func previewController(_ controller: QLPreviewController, previewItemAt index: Int) -> QLPreviewItem { previewURL! as NSURL }
}
