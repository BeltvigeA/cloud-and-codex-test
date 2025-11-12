import jwt from 'jsonwebtoken';

/**
 * Authentication middleware
 * Verifies JWT token from Authorization header
 */
export function authenticate(req, res, next) {
  try {
    // Get token from Authorization header
    const authHeader = req.headers.authorization;

    if (!authHeader || !authHeader.startsWith('Bearer ')) {
      return res.status(401).json({ error: 'No token provided' });
    }

    const token = authHeader.replace('Bearer ', '');

    // Verify token
    const jwtSecret = process.env.JWT_SECRET || 'your-secret-key-change-this';

    try {
      const decoded = jwt.verify(token, jwtSecret);

      // Attach user info to request
      req.user = {
        id: decoded.userId || decoded.sub || decoded.id,
        email: decoded.email,
        organizationId: decoded.organizationId
      };

      next();
    } catch (jwtError) {
      if (jwtError.name === 'TokenExpiredError') {
        return res.status(401).json({ error: 'Token expired' });
      }
      if (jwtError.name === 'JsonWebTokenError') {
        return res.status(401).json({ error: 'Invalid token' });
      }
      throw jwtError;
    }
  } catch (error) {
    console.error('Authentication error:', error);
    return res.status(500).json({ error: 'Authentication failed' });
  }
}

/**
 * Optional authentication middleware
 * Attaches user info if token is present, but doesn't fail if missing
 */
export function optionalAuthenticate(req, res, next) {
  try {
    const authHeader = req.headers.authorization;

    if (authHeader && authHeader.startsWith('Bearer ')) {
      const token = authHeader.replace('Bearer ', '');
      const jwtSecret = process.env.JWT_SECRET || 'your-secret-key-change-this';

      try {
        const decoded = jwt.verify(token, jwtSecret);
        req.user = {
          id: decoded.userId || decoded.sub || decoded.id,
          email: decoded.email,
          organizationId: decoded.organizationId
        };
      } catch (jwtError) {
        // Silently fail for optional auth
        console.warn('Optional auth failed:', jwtError.message);
      }
    }

    next();
  } catch (error) {
    console.error('Optional authentication error:', error);
    next(); // Continue even if error
  }
}

/**
 * Role-based authorization middleware
 * @param {Array<string>} allowedRoles - Array of allowed roles
 */
export function authorize(...allowedRoles) {
  return (req, res, next) => {
    if (!req.user) {
      return res.status(401).json({ error: 'Not authenticated' });
    }

    if (!req.user.role || !allowedRoles.includes(req.user.role)) {
      return res.status(403).json({ error: 'Insufficient permissions' });
    }

    next();
  };
}

export default {
  authenticate,
  optionalAuthenticate,
  authorize
};
