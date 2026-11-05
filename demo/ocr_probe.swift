// Read an image file or stdin ("-") and report only exact matches for the requested label.
// This is a diagnostic; it does not choose or execute a phone action.
import Foundation
import ImageIO
import Vision

guard CommandLine.arguments.count == 3 else {
    fputs("Usage: swift ocr_probe.swift IMAGE|- LABEL\n", stderr)
    exit(2)
}
let input = CommandLine.arguments[1]
let label = CommandLine.arguments[2]
let source: CGImageSource?
if input == "-" {
    let data = FileHandle.standardInput.readDataToEndOfFile()
    source = CGImageSourceCreateWithData(data as CFData, nil)
} else {
    source = CGImageSourceCreateWithURL(URL(fileURLWithPath: input) as CFURL, nil)
}
guard !label.isEmpty, let source,
      let image = CGImageSourceCreateImageAtIndex(source, 0, nil) else {
    fputs("Unable to decode image or empty label\n", stderr)
    exit(1)
}
let properties = CGImageSourceCopyPropertiesAtIndex(source, 0, nil) as? [CFString: Any]
let rawOrientation = (properties?[kCGImagePropertyOrientation] as? NSNumber)?.uint32Value ?? 1
let orientation = CGImagePropertyOrientation(rawValue: rawOrientation) ?? .up
let swapsDimensions = [.left, .right, .leftMirrored, .rightMirrored].contains(orientation)
let width = swapsDimensions ? image.height : image.width
let height = swapsDimensions ? image.width : image.height

let request = VNRecognizeTextRequest()
request.recognitionLevel = .accurate
request.recognitionLanguages = ["zh-Hans", "en-US"]
request.usesLanguageCorrection = false
do {
    try VNImageRequestHandler(cgImage: image, orientation: orientation, options: [:]).perform([request])
    let matches: [[String: Any]] = (request.results ?? []).compactMap { observation in
        guard let candidate = observation.topCandidates(1).first,
              candidate.string.trimmingCharacters(in: .whitespacesAndNewlines) == label else { return nil }
        let box = observation.boundingBox
        // Vision normalizes from the lower left; report image pixels from the top left.
        let x0 = box.minX * CGFloat(width)
        let y0 = (1 - box.maxY) * CGFloat(height)
        let x1 = box.maxX * CGFloat(width)
        let y1 = (1 - box.minY) * CGFloat(height)
        return ["confidence": candidate.confidence, "box_px": [x0, y0, x1, y1]]
    }
    let result: [String: Any] = ["width_px": width, "height_px": height, "matches": matches]
    let data = try JSONSerialization.data(withJSONObject: result, options: [.sortedKeys])
    print(String(decoding: data, as: UTF8.self))
} catch {
    fputs("Text recognition failed: \(type(of: error))\n", stderr)
    exit(1)
}
