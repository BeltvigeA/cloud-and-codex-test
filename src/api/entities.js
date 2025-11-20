// Entity classes for PrintPro3D API
import { organizationAPI, userAPI, userSettingsAPI } from './apiClient.js';

/**
 * Organization Entity
 */
export class Organization {
  constructor(data) {
    Object.assign(this, data);
  }

  static async getAll() {
    const data = await organizationAPI.getAll();
    return data.map(org => new Organization(org));
  }

  static async findById(id) {
    const data = await organizationAPI.findById(id);
    return new Organization(data);
  }

  static async create(orgData) {
    const data = await organizationAPI.create(orgData);
    return new Organization(data);
  }

  async update(updates) {
    const data = await organizationAPI.update(this.id, updates);
    Object.assign(this, data);
    return this;
  }

  async regenerateApiKey() {
    const data = await organizationAPI.regenerateApiKey(this.id);
    Object.assign(this, data);
    return this;
  }
}

/**
 * User Entity
 */
export class User {
  constructor(data) {
    Object.assign(this, data);
  }

  static async me() {
    const data = await userAPI.me();
    return new User(data);
  }

  async update(updates) {
    const data = await userAPI.update(updates);
    Object.assign(this, data);
    return this;
  }
}

/**
 * UserSettings Entity
 */
export class UserSettings {
  constructor(data) {
    Object.assign(this, data);
  }

  static async get() {
    const data = await userSettingsAPI.get();
    return data ? new UserSettings(data) : null;
  }

  static async create(settingsData) {
    const data = await userSettingsAPI.create(settingsData);
    return new UserSettings(data);
  }

  static async update(id, updates) {
    const data = await userSettingsAPI.update(id, updates);
    return new UserSettings(data);
  }
}

export default {
  Organization,
  User,
  UserSettings,
};
