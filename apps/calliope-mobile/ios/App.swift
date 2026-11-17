import UIKit
import Security

@main
final class AppDelegate: UIResponder, UIApplicationDelegate {
    var window: UIWindow?
    func application(_ application: UIApplication, open url: URL, options: [UIApplication.OpenURLOptionsKey: Any] = [:]) -> Bool {
        guard url.scheme == "calliope", url.absoluteString.count <= 4096 else { return false }
        NativeLinks.put(url.absoluteString)
        return true
    }

    func application(_ application: UIApplication,
                     didFinishLaunchingWithOptions options: [UIApplication.LaunchOptionsKey: Any]?) -> Bool {
        let window = UIWindow(frame: UIScreen.main.bounds)
        window.rootViewController = CalliopeController()
        window.makeKeyAndVisible()
        self.window = window
        return true
    }
    func applicationDidBecomeActive(_ application: UIApplication) { NativeAudio.shared.foreground(true); calliope_ios_active(true) }
    func applicationWillResignActive(_ application: UIApplication) { NativeAudio.shared.foreground(false); calliope_ios_active(false) }
}

enum NativeLinks {
    private static let lock = NSLock()
    private static var pending: String?
    static func put(_ value: String) { lock.lock(); defer { lock.unlock() }; pending = value }
    static func take() -> String { lock.lock(); defer { lock.unlock() }; let value = pending ?? ""; pending = nil; return value }
}

/// One UIKit-owned GPUI surface, constrained above the system keyboard.
final class CalliopeView: UIView {
    let gpuiWindow: UnsafeMutableRawPointer
    let contentController: UIViewController

    override init(frame: CGRect) {
        var directory = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("Calliope", isDirectory: true)
        do {
            try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true,
                attributes: [.protectionKey: FileProtectionType.completeUntilFirstUserAuthentication])
            var resources = URLResourceValues()
            resources.isExcludedFromBackup = true
            try directory.setResourceValues(resources)
        } catch { fatalError("Cannot prepare private Calliope storage: \(error)") }
        directory.path.withCString { calliope_ios_start($0) }
        guard let window = gpui_ios_get_window(), let controller = gpui_ios_view_controller(window) else {
            fatalError("Cannot create the Calliope GPUI surface")
        }
        gpuiWindow = window
        contentController = Unmanaged<UIViewController>.fromOpaque(controller).takeUnretainedValue()
        super.init(frame: frame)
        clipsToBounds = true
    }
    required init?(coder: NSCoder) { fatalError("init(coder:) is unsupported") }
    func attach(to parent: UIViewController) {
        parent.addChild(contentController)
        addSubview(contentController.view)
        contentController.didMove(toParent: parent)
    }
    override func layoutSubviews() {
        super.layoutSubviews()
        guard bounds.width > 0, bounds.height > 0, contentController.view.frame != bounds else { return }
        CATransaction.begin()
        CATransaction.setDisableActions(true)
        contentController.view.frame = bounds
        gpui_ios_layout_view(gpuiWindow)
        drawFrame()
        CATransaction.commit()
    }
    func drawFrame() { _ = gpui_ios_request_frame(gpuiWindow) }
}

final class CalliopeController: UIViewController {
    private var surface: CalliopeView!
    private var displayLink: CADisplayLink?
    override func viewDidLoad() {
        super.viewDidLoad()
        overrideUserInterfaceStyle = .dark
        view.backgroundColor = UIColor(red: 0.067, green: 0.094, blue: 0.153, alpha: 1)
        surface = CalliopeView(frame: .zero)
        surface.translatesAutoresizingMaskIntoConstraints = false
        view.addSubview(surface)
        surface.attach(to: self)
        NSLayoutConstraint.activate([
            surface.topAnchor.constraint(equalTo: view.safeAreaLayoutGuide.topAnchor),
            surface.leadingAnchor.constraint(equalTo: view.safeAreaLayoutGuide.leadingAnchor),
            surface.trailingAnchor.constraint(equalTo: view.safeAreaLayoutGuide.trailingAnchor),
            surface.bottomAnchor.constraint(equalTo: view.keyboardLayoutGuide.topAnchor),
        ])
    }
    override func viewDidAppear(_ animated: Bool) {
        super.viewDidAppear(animated)
        displayLink = CADisplayLink(target: self, selector: #selector(renderFrame))
        displayLink?.add(to: .main, forMode: .common)
    }
    override func viewWillDisappear(_ animated: Bool) {
        displayLink?.invalidate()
        displayLink = nil
        super.viewWillDisappear(animated)
    }
    @objc private func renderFrame() { surface.drawFrame() }
}

private enum ConnectionStore {
    static let query: [String: Any] = [
        kSecClass as String: kSecClassGenericPassword,
        kSecAttrService as String: "ai.rvbbit.calliope.mobile",
        kSecAttrAccount as String: "connection",
    ]
    static func load() throws -> String {
        var search = query
        search[kSecReturnData as String] = true
        search[kSecMatchLimit as String] = kSecMatchLimitOne
        var result: CFTypeRef?
        let status = SecItemCopyMatching(search as CFDictionary, &result)
        if status == errSecItemNotFound { return "{}" }
        guard status == errSecSuccess, let data = result as? Data,
              let value = String(data: data, encoding: .utf8) else { throw keychainError(status) }
        return value
    }
    static func save(_ value: String) throws {
        let attributes: [String: Any] = [
            kSecValueData as String: Data(value.utf8),
            kSecAttrAccessible as String: kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly,
        ]
        var status = SecItemUpdate(query as CFDictionary, attributes as CFDictionary)
        if status == errSecItemNotFound {
            status = SecItemAdd(query.merging(attributes) { _, new in new } as CFDictionary, nil)
        }
        guard status == errSecSuccess else { throw keychainError(status) }
    }
    static func keychainError(_ status: OSStatus) -> NSError {
        NSError(domain: NSOSStatusErrorDomain, code: Int(status), userInfo: [
            NSLocalizedDescriptionKey: "Calliope Keychain access failed (\(status)). Unlock the device and sign in again."
        ])
    }
}

private var lastInputKind = ""
private func configureInput(_ kind: String) {
    guard kind != lastInputKind else { return }
    let windows = (UIApplication.shared.delegate as? AppDelegate)?.window.map { [$0] } ?? []
    func find(_ view: UIView) -> UITextView? {
        if let text = view as? UITextView { return text }
        for child in view.subviews { if let found = find(child) { return found } }
        return nil
    }
    guard let input = windows.compactMap({ find($0) }).first else { return }
    lastInputKind = kind
    input.isSecureTextEntry = kind == "password"
    input.keyboardType = ["url": UIKeyboardType.URL, "email": .emailAddress,
                          "number": .numbersAndPunctuation, "decimal": .decimalPad,
                          "phone": .phonePad][kind] ?? .default
    input.autocorrectionType = .no
    input.autocapitalizationType = .none
    if input.isFirstResponder { input.reloadInputViews() }
}

@_cdecl("calliope_ios_request")
func hostRequest(_ pointer: UnsafePointer<CChar>) -> UnsafeMutablePointer<CChar>? {
    let reply: [String: Any]
    do {
        guard let data = String(cString: pointer).data(using: .utf8),
              let request = try JSONSerialization.jsonObject(with: data) as? [String: Any] else {
            throw NSError(domain: "Calliope", code: 1, userInfo: [NSLocalizedDescriptionKey: "Invalid host request"])
        }
        switch request["op"] as? String {
        case "audio": reply = try NativeAudio.shared.command(request)
        case "pick": reply = try NativeMedia.pick(request)
        case "preview": reply = try NativeMedia.preview(request)
        case "load_connection": reply = ["value": try ConnectionStore.load()]
        case "take_link": reply = ["value": NativeLinks.take()]
        case "save_connection":
            guard let value = request["value"] as? String else { throw ConnectionStore.keychainError(errSecParam) }
            try ConnectionStore.save(value)
            reply = ["ok": true]
        case "open_url":
            guard let value = request["url"] as? String, let url = URL(string: value),
                  ["https", "http"].contains(url.scheme), url.user == nil, url.password == nil else {
                throw NSError(domain: "Calliope", code: 2, userInfo: [NSLocalizedDescriptionKey: "Invalid browser URL"])
            }
            DispatchQueue.main.async { UIApplication.shared.open(url) }
            reply = ["ok": true]
        case "input_kind":
            let kind = request["kind"] as? String ?? "text"
            DispatchQueue.main.async { configureInput(kind) }
            reply = ["ok": true]
        default: reply = ["error": "Unknown iOS host operation"]
        }
    } catch { reply = ["error": error.localizedDescription] }
    guard let data = try? JSONSerialization.data(withJSONObject: reply), let text = String(data: data, encoding: .utf8) else { return nil }
    return strdup(text)
}
@_cdecl("calliope_ios_free")
func hostFree(_ pointer: UnsafeMutablePointer<CChar>?) { free(pointer) }
@_cdecl("calliope_ios_log")
func hostLog(_ pointer: UnsafePointer<CChar>) { NSLog("%@", String(cString: pointer)) }
