# Changelog

## v2.1

- **CSV Result Grouping**: Results grouped by container name for easier analysis
- **WebSocket-Specific Metrics**: Enhanced WebSocket CSV format with latency and throughput data
- **Super Quick Testing**: New `run-super-quick` option for fastest validation
- **Enhanced Port Management**: All containers now use port 80 internally for consistency

## v2.0

- **Simplified Port Management**: Fixed host port with automatic container port detection
- **Enhanced Health Checks**: Comprehensive validation with HTTP and WebSocket testing
- **Auto-Discovery**: Intelligent container detection from directory structure
- **Improved Automation**: Streamlined Makefile commands and script integration
- **Better Error Handling**: Robust error detection and reporting
- **Configurable Ports**: Environment variable support for custom ports

### Key Changes

- Removed complex port arrays and manual configuration
- Added real HTTP response validation in health checks
- Implemented WebSocket handshake testing
- Enhanced container discovery and management
- Improved repository cleaning and maintenance tools
- Added comprehensive logging and error reporting
