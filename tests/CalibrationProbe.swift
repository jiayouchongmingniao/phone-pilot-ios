import Foundation

// Research-only comparison against the evolving Python calibration; never used to move the pointer.
struct Measurement: Decodable { let x: Double, y: Double, px: Double, py: Double }
struct Fit: Encodable { let gain: [Double], offset: [Double], rms: [Double] }
enum FitError: Error { case invalid }

func fit(_ samples: [Measurement]) throws -> Fit {
    guard samples.count >= 5 else { throw FitError.invalid }
    var gains = [Double](), offsets = [Double](), rmsValues = [Double]()
    for axis in 0..<2 {
        let units = samples.map { axis == 0 ? $0.x : $0.y }
        let positions = samples.map { axis == 0 ? $0.px : $0.py }
        guard (units + positions).allSatisfy(\.isFinite),
              positions.max()! - positions.min()! >= (axis == 0 ? 40 : 80) else { throw FitError.invalid }
        let u = units.reduce(0, +) / Double(units.count)
        let p = positions.reduce(0, +) / Double(positions.count)
        let variance = units.reduce(0) { $0 + pow($1 - u, 2) }
        guard variance > 0 else { throw FitError.invalid }
        let covariance = zip(units, positions).reduce(0.0) { $0 + ($1.0 - u) * ($1.1 - p) }
        let gain = covariance / variance, offset = p - gain * u
        let errors = zip(units, positions).map { $1 - (gain * $0 + offset) }
        let rms = sqrt(errors.reduce(0) { $0 + $1 * $1 } / Double(errors.count))
        guard (0.1...3).contains(gain), abs(offset) <= 80, rms <= 3,
              errors.map({ abs($0) }).max()! <= 6 else { throw FitError.invalid }
        gains.append(gain); offsets.append(offset); rmsValues.append(rms)
    }
    return Fit(gain: gains, offset: offsets, rms: rmsValues)
}

@main struct Runner {
    static func main() throws {
        let cases = try JSONDecoder().decode([[Measurement]].self, from: FileHandle.standardInput.readDataToEndOfFile())
        let results: [[String: [Double]]?] = cases.map { measurements in
            guard let result = try? fit(measurements) else { return nil }
            return ["gain": result.gain, "offset": result.offset, "rms": result.rms]
        }
        FileHandle.standardOutput.write(try JSONEncoder().encode(results))
    }
}
