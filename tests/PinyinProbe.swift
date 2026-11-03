import Foundation

// Research only. System transliteration produces one reading, not a list of alternatives.
let inputs = try JSONDecoder().decode([String].self, from: FileHandle.standardInput.readDataToEndOfFile())
let outputs = inputs.map { text -> String in
    let result = NSMutableString(string: text)
    CFStringTransform(result, nil, kCFStringTransformMandarinLatin, false)
    CFStringTransform(result, nil, kCFStringTransformStripCombiningMarks, false)
    return (result as String).lowercased().filter { $0 >= "a" && $0 <= "z" }
}
let encoded = try JSONEncoder().encode(outputs)
FileHandle.standardOutput.write(encoded)
